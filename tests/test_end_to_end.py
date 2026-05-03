from pathlib import Path

from vaultnotes import build, config as cfgmod, integrity, rag, sync

FIXTURES = Path(__file__).parent / "fixtures" / "mini_vault"


def _make_cfg(tmp_path: Path) -> cfgmod.Config:
    return cfgmod.Config(
        site_title="Test Notes",
        wordmark="TN",
        theme="midnight",
        accent=None,
        vault_path=FIXTURES,
        projects=[
            cfgmod.Project(folder="ProjA", label="ProjA", color="#f5a833",
                           description="First project."),
            cfgmod.Project(folder="ProjB", label="ProjB", color="#a37cff",
                           description="Second."),
        ],
        github_repo="test/test.github.io",
        github_branch="main",
        local_clone=tmp_path / "pages",
        schedule_enabled=True,
        schedule_time="17:00",
    )


def test_sync_build_integrity(tmp_path):
    cfg = _make_cfg(tmp_path)
    cfg.local_clone.mkdir(parents=True)
    sync.sync_all(cfg)
    build.build(cfg, cfg.local_clone)

    html = (cfg.local_clone / "notes.html").read_text()
    assert "ProjA" in html
    assert "ProjB" in html
    assert "// AUTO-FILES:ProjA:START" in html
    assert "// AUTO-FILES:ProjA:END" in html
    assert "Test Notes" in html
    assert "entry.md" in html
    assert "index.md" in html
    assert r"(\d{2}|\d{4})" in html

    errs = integrity.check(cfg, cfg.local_clone)
    assert errs == [], errs


def test_rag_refresh_is_idempotent(tmp_path):
    cfg = _make_cfg(tmp_path)
    cfg.rag.enabled = True
    cfg.local_clone.mkdir(parents=True)

    rag.enable(cfg, cfg.local_clone)
    before = {
        "script": (cfg.local_clone / "scripts" / "index-notes.mjs").read_text(),
        "chat": (cfg.local_clone / "chat" / "chat.js").read_text(),
        "config": (cfg.local_clone / "rag-config.json").read_text(),
    }

    rag.enable(cfg, cfg.local_clone)
    after = {
        "script": (cfg.local_clone / "scripts" / "index-notes.mjs").read_text(),
        "chat": (cfg.local_clone / "chat" / "chat.js").read_text(),
        "config": (cfg.local_clone / "rag-config.json").read_text(),
    }

    assert after == before
    assert '"chunkWords": 500' in after["config"]
    assert "embeddingCacheKey" in after["script"]
    assert "expandTemporalQuery" in after["chat"]


def test_validate_rejects_bad_hex(tmp_path):
    cfg = _make_cfg(tmp_path)
    cfg.projects[0].color = "not-a-color"
    errs = cfgmod.validate(cfg)
    assert any("color" in e for e in errs)
