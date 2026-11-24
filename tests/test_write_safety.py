"""Write-path safety through the real OnshapeBackend (scripted transport).

Residual A: a post-write version taken from a SEPARATE GET may include a human
edit Claude never read, so it must not become `last_read`.
Residual C: a redirect in reply to a POST is an unknown outcome; a created
variable gets an accurate re-read message.
"""
import json

import httpx
import pytest

from onshape_cad_mcp.backends.onshape import OnshapeBackend
from onshape_cad_mcp.budget import Ledger
from onshape_cad_mcp.cache import DocCache
from onshape_cad_mcp.config import Config
from onshape_cad_mcp.docref import DocRef
from onshape_cad_mcp.tools import Context, cad_sketch, cad_variable

REF = DocRef("a" * 24, "b" * 24, "c" * 24)
SPEC = {"plane": "Front",
        "entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [10, 0]}],
        "constraints": [{"type": "ground", "refs": ["l.start"]}, {"type": "horizontal", "refs": ["l"]}],
        "dimensions": [{"type": "length", "ref": "l", "value": 10}]}


class Doc:
    """Scripted Onshape: `post_reply` is the POST body, `version_after_post` what
    the document is at when the follow-up GET arrives (a human edit landed)."""

    def __init__(self, post_reply, version_after_post="mv-human", post_status=200, post_headers=None):
        self.version = "mv1"
        self.post_reply = post_reply
        self.version_after_post = version_after_post
        self.post_status = post_status
        self.post_headers = post_headers or {}
        self.posts = 0

    def handler(self, req):
        if req.method == "POST":
            self.posts += 1
            self.version = self.version_after_post
            return httpx.Response(self.post_status, json=self.post_reply, headers=self.post_headers)
        if req.url.path.endswith("currentmicroversion"):
            return httpx.Response(200, json={"microversionId": self.version})
        return httpx.Response(200, json={"sourceMicroversion": self.version,
                                         "featureStates": {}, "features": []})


@pytest.fixture(autouse=True)
def creds(monkeypatch):
    monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "k")
    monkeypatch.setenv("ONSHAPE_SECRET_KEY", "s")


def ctx_for(tmp_path, doc):
    cfg = Config(budget=100, ledger_path=tmp_path / "l.json")
    ledger = Ledger(cfg.ledger_path, 100)
    backend = OnshapeBackend(cfg, ledger, httpx.MockTransport(doc.handler))
    return Context(backend, DocCache(backend), ledger, cfg), ledger


FEATURE_REPLY = {"feature": {"featureId": "F9", "featureType": "newSketch", "name": "S"},
                 "featureState": {"featureStatus": "OK"}}


@pytest.mark.parametrize("src", [None, "mv1"], ids=["missing", "echoed"])
def test_sketch_refetched_version_is_not_adopted(tmp_path, src):
    reply = dict(FEATURE_REPLY, **({} if src is None else {"sourceMicroversion": src}))
    doc = Doc(reply)
    ctx, _ = ctx_for(tmp_path, doc)
    ctx.read(REF)
    out = cad_sketch(ctx, REF, "Rail", SPEC, apply=True)
    assert out["applied"] is True
    assert "cad_read" in out["note"]
    assert REF not in ctx.last_read          # the human-edited version was never read
    assert ctx.cache.peek(REF) is None       # no splice into the old list
    posts = doc.posts
    again = cad_sketch(ctx, REF, "Rail2", SPEC, apply=True)
    assert again["applied"] is False and "cad_read" in again["error"]
    assert doc.posts == posts                # nothing was sent
    ctx.read(REF)                            # after a real read writes work again
    assert ctx.last_read[REF] == "mv-human"
    assert cad_sketch(ctx, REF, "Rail2", SPEC, apply=True)["applied"] is True


@pytest.mark.parametrize("src", [None, "mv1"], ids=["missing", "echoed"])
def test_variable_refetched_version_is_not_adopted(tmp_path, src):
    reply = {} if src is None else {"sourceMicroversion": src}
    doc = Doc(reply)
    ctx, _ = ctx_for(tmp_path, doc)
    ctx.read(REF)
    out = cad_variable(ctx, REF, "w", "5 mm", apply=True)
    assert out["applied"] is True and "cad_read" in out["note"]
    assert REF not in ctx.last_read and ctx.cache.peek(REF) is None
    again = cad_variable(ctx, REF, "w", "6 mm", apply=True)
    assert again["applied"] is False and "cad_read" in again["error"]
    assert doc.posts == 1


def test_distinct_reply_version_is_adopted(tmp_path):
    doc = Doc(dict(FEATURE_REPLY, sourceMicroversion="mv2"))
    ctx, _ = ctx_for(tmp_path, doc)
    ctx.read(REF)
    out = cad_sketch(ctx, REF, "Rail", SPEC, apply=True)
    assert out["applied"] is True and "note" not in out
    assert ctx.last_read[REF] == "mv2"
    assert ctx.cache.peek(REF).microversion == "mv2"


def test_redirect_to_a_post_is_unknown_not_applied_false(tmp_path):
    doc = Doc({}, post_status=307, post_headers={"Location": "/api/elsewhere"})
    ctx, _ = ctx_for(tmp_path, doc)
    before = ctx.read(REF).microversion
    out = cad_sketch(ctx, REF, "Rail", SPEC, apply=True)
    assert out["applied"] == "unknown"
    assert "cad_read" in out["error"] and "may have landed" in out["error"]
    assert ctx.last_read[REF] == before and ctx.cache.peek(REF) is None
    out = cad_variable(ctx, REF, "w", "5 mm", apply=True)
    assert out["applied"] is False and "cad_read" in out["error"]  # cache dropped, no retry


def test_skew_error_says_may_have_landed_once(tmp_path):
    doc = Doc({"microversionSkew": True})
    ctx, _ = ctx_for(tmp_path, doc)
    ctx.read(REF)
    out = cad_sketch(ctx, REF, "Rail", SPEC, apply=True)
    assert out["applied"] == "unknown"
    assert out["error"].count("may have landed") == 1


def test_creating_a_variable_then_writing_gives_an_accurate_message(tmp_path):
    doc = Doc({"sourceMicroversion": "mv2"}, version_after_post="mv2")  # no human edit
    ctx, _ = ctx_for(tmp_path, doc)
    ctx.read(REF)
    out = cad_variable(ctx, REF, "w", "5 mm", apply=True)
    assert out["applied"] is True and "variable" in out["note"] and "cad_read" in out["note"]
    nxt = cad_variable(ctx, REF, "w", "6 mm", apply=True)
    assert nxt["applied"] is False
    assert "variable" in nxt["error"] and "document changed" not in nxt["error"]
    assert "stale" not in nxt["error"]
    ctx.read(REF)
    assert "variable" not in ctx.reread.get(REF, "")
