import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import veilwave as vw


def test_selftest_passes():
    assert vw.selftest(os.environ.get("PAIR_DIR"))


def test_reconstruction_without_processing():
    x = np.random.default_rng(1).standard_normal(16000) * 0.1
    y = vw.VeilWave(stages=dict(wiener=False, agc=False)).process(x)
    assert np.max(np.abs(y - x)) < 1e-6


def test_output_is_finite_and_same_length():
    x = np.random.default_rng(2).standard_normal(20000) * 0.05
    y = vw.VeilWave().process(x)
    assert len(y) == len(x) and np.isfinite(y).all()


def test_uart_roundtrip():
    rng = np.random.default_rng(3)
    s = rng.integers(0, 1024, 50 * 32)
    p = vw.PacketParser()
    got = p.feed(b"".join(vw.pack_samples(s[i * 32:(i + 1) * 32], i) for i in range(50)))
    assert np.array_equal(got, s[:len(got)]) and len(got) >= 49 * 32



def test_uart_parser_handles_fragmented_input():
    samples = np.arange(64, dtype=np.int64) % 1024
    packet1 = vw.pack_samples(samples[:32], 10)
    packet2 = vw.pack_samples(samples[32:], 11)
    packet3 = vw.pack_samples(np.zeros(32, dtype=np.int64), 12)
    parser = vw.PacketParser()
    got = []
    stream = packet1 + packet2 + packet3
    for i in range(0, len(stream), 7):
        got.extend(parser.feed(stream[i:i + 7]))
    assert np.array_equal(np.asarray(got), samples)


def test_threshold_file_is_loaded_and_validated(tmp_path):
    import json
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps({"theta_lf": 0.5265, "theta_var": 0.9136}))
    pipeline = vw.build_pipeline(str(path))
    assert pipeline.cfg.theta_lf == 0.5265
    assert pipeline.cfg.theta_var == 0.9136


def test_invalid_threshold_file_is_rejected(tmp_path):
    import json
    path = tmp_path / "bad_thresholds.json"
    path.write_text(json.dumps({"theta_lf": 0.5}))
    try:
        vw.build_pipeline(str(path))
    except ValueError as exc:
        assert "threshold file" in str(exc)
    else:
        raise AssertionError("incomplete threshold file should be rejected")



def test_deep_filter_weights_can_be_loaded_without_mask_weights(tmp_path):
    if not vw.HAVE_TORCH:
        import pytest
        pytest.skip("PyTorch is optional")
    weights = tmp_path / "df_net.pt"
    vw.torch.save(vw.DFNet().state_dict(), weights)
    pipeline = vw.build_pipeline(df_weights=str(weights))
    assert pipeline.df_net is not None
    assert pipeline.mask_net is None
