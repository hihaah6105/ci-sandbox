"""
run_local.py - Smoke-test the harness on your own machine with no Docker, no
evaluator and no model. It fakes the task container with a temporary directory.

    python3 run_local.py <brief.txt> [workdir]

What it proves: the contract parses, placeholders land at the right absolute
paths, and the conformance gate reads them back. What it cannot prove: anything
about engineering quality or score - that needs a real run.
"""
import asyncio, os, shutil, subprocess, sys, tempfile

import harness


class FakeSession:
    def __init__(self, instruction, workdir, root):
        self.instruction, self.workdir, self.root = instruction, workdir, root
        self.final_message = ""

    def _real(self, p):
        return os.path.join(self.root, p.lstrip("/"))

    def time_left(self):
        return 3600

    async def exec(self, command, timeout_sec=120):
        r = subprocess.run(["bash", "-c", command], cwd=self.root,
                           capture_output=True, text=True, timeout=timeout_sec)
        return {"exit_code": r.returncode, "output": (r.stdout + r.stderr)[:20000],
                "truncated": False, "timed_out": False}

    async def read_file(self, path):
        fp = self._real(path)
        if not os.path.isfile(fp):
            raise RuntimeError("no such file")
        return open(fp, "rb").read()

    async def write_file(self, path, data, append=False):
        fp = self._real(path)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        data = data.encode() if isinstance(data, str) else data
        open(fp, "ab" if append else "wb").write(data)
        return os.path.getsize(fp)

    async def log(self, text):
        print(f"[log] {text[:400]}")

    async def usage(self, *a, **k):
        pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    brief = open(sys.argv[1], encoding="utf8", errors="ignore").read()
    workdir = sys.argv[2] if len(sys.argv) > 2 else "/filesystem"
    root = tempfile.mkdtemp(prefix="ple-local-")
    try:
        session = FakeSession(brief, workdir, root)
        message = asyncio.run(harness.solve(session))
        print("\n--- files created ---")
        for dirpath, _, names in os.walk(root):
            for n in sorted(names):
                fp = os.path.join(dirpath, n)
                print(f"{os.path.getsize(fp):>9d}  /{os.path.relpath(fp, root)}")
        print("\n--- final message ---")
        print(message)
    finally:
        shutil.rmtree(root, ignore_errors=True)
