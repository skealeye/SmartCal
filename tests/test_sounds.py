import io
import wave

from smartcal import sounds, toast
from smartcal.settings import Settings
from tests.test_tray import build


def test_every_sound_is_a_short_valid_wav():
    for name in sounds.SOUNDS:
        with wave.open(io.BytesIO(sounds.wav_bytes(name))) as w:
            assert w.getnchannels() == 1 and w.getframerate() == sounds.RATE
            assert 0.05 < w.getnframes() / w.getframerate() < 1.0


def test_play_is_silent_when_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr("winsound.PlaySound", lambda *a: calls.append(a))
    assert sounds.play("chime") is None       # SMARTCAL_NO_SOUND=1 in the tests
    assert calls == []
    monkeypatch.delenv("SMARTCAL_NO_SOUND")
    sounds.set_enabled(False)
    assert sounds.play("chime") is None
    assert calls == []
    sounds.set_enabled(True)
    sounds.play("chime").join(2)
    assert len(calls) == 1
    flags = calls[0][1]
    import winsound
    assert flags & winsound.SND_MEMORY and not flags & winsound.SND_ASYNC   # Windows refuses both


def test_play_never_raises(monkeypatch):
    monkeypatch.delenv("SMARTCAL_NO_SOUND")
    def boom(*a):
        raise RuntimeError("no audio device")
    monkeypatch.setattr("winsound.PlaySound", boom)
    sounds.play("open").join(2)


def test_toast_plays_the_chime(monkeypatch):
    played = []
    monkeypatch.setattr(toast.sounds, "play", played.append)

    class Note:
        def __init__(self, **kw): pass
        def show(self): pass
    toast.ToastSender(notification_cls=Note)("Standup", "in 10 minutes")
    assert played == ["chime"]


def test_sounds_setting_roundtrip_and_tray_switch(qtbot, tmp_path):
    tray, _ = build(qtbot, tmp_path)
    assert tray.settings.sounds is True and tray._sounds_action.isChecked()
    tray.set_sounds(False)
    assert Settings(tmp_path / "s.json").sounds is False
    sounds.set_enabled(True)


def test_test_notification_uses_the_toast_sender(qtbot, tmp_path):
    sent = []
    tray, _ = build(qtbot, tmp_path, send_test_fn=lambda *a: sent.append(a))
    tray.send_test()
    assert sent and sent[0][0] == "SmartCal test notification"


def test_volume_scales_the_sound_and_zero_is_silent(monkeypatch):
    import struct

    def peak(pct):
        raw = wave.open(io.BytesIO(sounds.wav_bytes("chime", pct))).readframes(10**6)
        return max(abs(v) for v in struct.unpack(f"<{len(raw) // 2}h", raw))
    assert peak(100) > peak(40) > peak(10) > 0
    assert abs(peak(40) / peak(100) - 0.4) < 0.02
    monkeypatch.delenv("SMARTCAL_NO_SOUND")
    sounds.set_volume(0)
    try:
        assert sounds.play("chime") is None          # volume 0: nothing plays
    finally:
        sounds.set_volume(sounds.DEFAULT_VOLUME)


def test_volume_defaults_to_40_and_is_saved(tmp_path):
    assert sounds.DEFAULT_VOLUME == 40
    s = Settings(tmp_path / "s.json")
    assert s.sound_volume == 40
    s.sound_volume = 75
    s.save()
    assert Settings(tmp_path / "s.json").sound_volume == 75
    assert sounds.clamp_volume(150) == 100 and sounds.clamp_volume("x") == 40


def test_settings_dialog_slider_saves_the_volume(qtbot, tmp_path):
    from tests.test_settings_dialog import build
    dlg, settings, *_ = build(qtbot, tmp_path)
    assert dlg.volume_slider.value() == 40 and dlg.volume_label.text() == "40%"
    dlg.volume_slider.setValue(65)
    assert dlg.volume_label.text() == "65%"
    dlg.accept()
    assert Settings(settings.path).sound_volume == 65
    sounds.set_volume(sounds.DEFAULT_VOLUME)
