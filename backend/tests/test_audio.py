"""T5 acceptance: idempotency + manifest — uses a fake synthesizer (no network)."""
import json
from pathlib import Path

from app.audio.gen_audio import generate
from app.protocol_loader import get_deck


def _fake_synth(text, out_path, voice):
    out_path.write_bytes(b"FAKEMP3" + text.encode("utf-8"))


def test_idempotent_second_run_regenerates_nothing(tmp_path):
    deck = get_deck()
    n1 = generate(deck, tmp_path, synth=_fake_synth)
    assert n1 == len(deck)
    n2 = generate(deck, tmp_path, synth=_fake_synth)
    assert n2 == 0
    # each deck clip has a file + manifest entry
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert len(manifest) == len(deck)
    for cid in deck:
        assert (tmp_path / f"{cid}.mp3").exists()
        assert (tmp_path / f"{cid}.mp3").stat().st_size > 5


def test_force_regenerates_all(tmp_path):
    deck = get_deck()
    generate(deck, tmp_path, synth=_fake_synth)
    n = generate(deck, tmp_path, force=True, synth=_fake_synth)
    assert n == len(deck)