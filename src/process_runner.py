"""Run archive tools without blocking cancellation on a quiet stdout pipe."""
import subprocess
import tempfile
from pathlib import Path

from models import CancellationToken, JobCallbacks, RuntimeState
from utils import redact_command


def run_process(command: list[str], token: CancellationToken, job_id: int,
                callbacks: JobCallbacks, runtime: RuntimeState | None = None,
                cwd: Path | None = None) -> bool:
    token.raise_if_requested(job_id)
    callbacks.log_cb(f"  [CMD] {redact_command(command)}", "#8b949e")
    # Disk-backed output avoids pipe deadlocks and unbounded memory consumption.
    with tempfile.TemporaryFile(mode="w+b") as output:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT, cwd=cwd)
        if runtime:
            runtime.set_process(job_id, process)
        try:
            while process.poll() is None:
                token.raise_if_requested(job_id)
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
            token.raise_if_requested(job_id)
            output.seek(max(0, output.tell() - 32768))
            lines = output.read().decode("utf-8", errors="replace").splitlines()
            if process.returncode:
                for line in lines[-15:]:
                    callbacks.log_cb(f"  [7z] {line}", "#f85149")
            elif callbacks.verbose_cb:
                for line in lines:
                    callbacks.verbose_cb(line)
            return process.returncode == 0
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            if runtime:
                runtime.set_process(job_id, None)
