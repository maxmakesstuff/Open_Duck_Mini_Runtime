"""
Standalone tests for the droid_voice command-line tool (no pytest).

Run from this directory:  python3 test_cli.py
"""
import glob
import os
import subprocess
import sys
import tempfile
import wave

CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "droid_voice.py")


def run(*args, env=None):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True, env=env)


def wavs(d):
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(d, "*.wav")))


def test_mood_writes_named_wav():
    with tempfile.TemporaryDirectory() as d:
        r = run("--mood", "happy", "--seed", "3", "--out", d)
        assert r.returncode == 0, r.stderr
        assert wavs(d) == ["happy_e050_s3.wav"]
        with wave.open(os.path.join(d, "happy_e050_s3.wav")) as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 44100)


def test_same_seed_same_bytes():
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        run("--mood", "curious", "--seed", "5", "--out", a)
        run("--mood", "curious", "--seed", "5", "--out", b)
        name = "curious_e050_s5.wav"
        assert open(os.path.join(a, name), "rb").read() == open(os.path.join(b, name), "rb").read()


def test_count_with_seed_counts_up():
    with tempfile.TemporaryDirectory() as d:
        run("--mood", "sad", "--seed", "10", "--count", "3", "--out", d)
        assert wavs(d) == ["sad_e050_s10.wav", "sad_e050_s11.wav", "sad_e050_s12.wav"]


def test_count_without_seed_never_overwrites():
    with tempfile.TemporaryDirectory() as d:
        r = run("--mood", "yes", "--count", "50", "--out", d)
        assert r.returncode == 0, r.stderr
        names = wavs(d)
        assert len(names) == 50
        for name in names:                       # the seed in the name reproduces the file
            assert name in r.stdout


def test_block_writes_named_wav():
    with tempfile.TemporaryDirectory() as d:
        assert run("--block", "bell", "--seed", "1", "--out", d).returncode == 0
        assert wavs(d) == ["block_bell_s1.wav"]


def test_unknown_mood_is_a_clean_error():
    with tempfile.TemporaryDirectory() as d:
        r = run("--mood", "grumpy", "--out", d)
        assert r.returncode == 2
        assert "happy" in r.stderr and "Traceback" not in r.stderr
        assert wavs(d) == []


def test_unknown_block_is_a_clean_error():
    r = run("--block", "kazoo")
    assert r.returncode == 2 and "chatter" in r.stderr and "Traceback" not in r.stderr


def test_nothing_to_do_is_a_clean_error():
    r = run()
    assert r.returncode == 2 and "Traceback" not in r.stderr


def test_energy_out_of_range_is_clamped():
    with tempfile.TemporaryDirectory() as d:
        r = run("--mood", "happy", "--seed", "1", "--energy", "3", "--out", d)
        assert r.returncode == 0, r.stderr
        assert wavs(d) == ["happy_e100_s1.wav"]
        with wave.open(os.path.join(d, "happy_e100_s1.wav")) as w:
            assert w.getnframes() / w.getframerate() < 6.0


def test_file_name_reproduces_the_file_even_for_odd_energies():
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        run("--mood", "yes", "--seed", "1", "--energy", "0.504", "--out", a)
        run("--mood", "yes", "--seed", "1", "--energy", "0.5", "--out", b)
        name = "yes_e050_s1.wav"
        assert open(os.path.join(a, name), "rb").read() == open(os.path.join(b, name), "rb").read()


def test_bad_numbers_are_clean_errors():
    with tempfile.TemporaryDirectory() as d:
        for args in (("--mood", "yes", "--seed", "-1"), ("--mood", "yes", "--count", "2000000"),
                     ("--mood", "yes", "--count", "0"), ("--mood", "yes", "--energy", "nan"),
                     ("--mood", "yes", "--block", "bell"), ("--all", "--mood", "yes")):
            r = run(*args, "--out", d)
            assert r.returncode == 2 and "Traceback" not in r.stderr, (args, r.stderr)
        assert wavs(d) == []


def test_out_pointing_at_a_file_is_a_clean_error():
    with tempfile.NamedTemporaryFile() as f:
        r = run("--mood", "yes", "--out", f.name)
        assert r.returncode == 2 and "Traceback" not in r.stderr, r.stderr


def test_play_without_a_player_still_writes_the_file():
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ, PATH="/nonexistent")
        r = run("--mood", "no", "--seed", "1", "--out", d, "--play", env=env)
        assert r.returncode == 0, r.stderr
        assert wavs(d) == ["no_e050_s1.wav"]
        assert "afplay" in r.stdout and "Traceback" not in r.stderr


def test_all_writes_the_starter_set():
    with tempfile.TemporaryDirectory() as d:
        r = run("--all", "--out", d)
        assert r.returncode == 0, r.stderr
        names = wavs(d)
        assert len(names) == 9 * 3 + 8, len(names)
        assert sum(n.startswith("block_") for n in names) == 8


def test_list_names_everything():
    r = run("--list")
    assert r.returncode == 0
    for name in ("happy", "hello", "curious", "excited", "sad", "beatbox", "alert", "yes", "no",
                 "chatter", "glide", "word", "question", "boopbip", "chirp", "bell", "sweep"):
        assert name in r.stdout, name


def test_bench_reports_every_mood():
    r = run("--bench")
    assert r.returncode == 0, r.stderr
    lines = [l for l in r.stdout.splitlines() if "x real time" in l]
    assert len(lines) >= 9


def test_out_dir_is_created():
    with tempfile.TemporaryDirectory() as d:
        sub = os.path.join(d, "a", "b")
        assert run("--mood", "alert", "--seed", "1", "--out", sub).returncode == 0
        assert wavs(sub) == ["alert_e050_s1.wav"]


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"\nAll {len(tests)} droid_voice CLI tests passed.")
