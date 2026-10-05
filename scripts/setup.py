"""Install the pinned read-only backend into a private Windows virtual environment."""
import hashlib, os, pathlib, subprocess, sys, tempfile, urllib.request, venv
BASE = pathlib.Path(os.environ.get("LOCALAPPDATA", str(pathlib.Path.home() / "AppData" / "Local"))) / "wechat2codex"
URL = "https://files.pythonhosted.org/packages/52/d9/8853054ae50bec595bfff518aa3dcd5d704c27ce4f8221469db7ef279308/wechatauto_replica-1.2.4.4-py3-none-any.whl"
SHA = "2a0a81884c5ea74d04d15246d7c88ba96b0c9afcfd773b89762cefab5e09a551"
def call(args):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.run(args, check=True, creationflags=flags)
def main():
    if os.name != "nt":
        raise SystemExit("This backend currently supports Windows desktop WeChat only.")
    BASE.mkdir(parents=True,exist_ok=True)
    env = BASE / "venv"
    # Spawn the venv module hidden as well (venv may otherwise open a child console).
    if not (env / "Scripts/python.exe").exists():
        original = subprocess.Popen
        class HiddenPopen(original):
            def __init__(self, *args, **kwargs):
                kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
                super().__init__(*args, **kwargs)
        subprocess.Popen = HiddenPopen
        try:
            venv.create(env, with_pip=True)
        finally:
            subprocess.Popen = original
    py = str(env / "Scripts/python.exe")
    requirements = pathlib.Path(__file__).resolve().parents[1] / "requirements-read.txt"
    call([py,"-m","pip","install","--disable-pip-version-check","--only-binary=:all:","-r",str(requirements)])
    with tempfile.TemporaryDirectory(prefix="wechat2codex-") as tmp:
        wheel = pathlib.Path(tmp) / "wechatauto_replica-1.2.4.4-py3-none-any.whl"
        urllib.request.urlretrieve(URL,wheel)
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != SHA:
            raise RuntimeError("Upstream wheel SHA256 mismatch")
        call([py,"-m","pip","install","--disable-pip-version-check","--no-deps",str(wheel)])
    print("Ready: " + py)
if __name__ == "__main__":
    main()
