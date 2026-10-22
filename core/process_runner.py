"""Bounded, cancellable subprocesses owned by the current automation task."""
import subprocess
import tempfile
import time
from core.automation_runtime import get_runtime


def run_process(command, *, cwd=None, context=None, timeout=120, env=None,
                max_output_bytes=16000, allow_truncated=True, binary_output=False):
    with tempfile.TemporaryFile() as output:
        with get_runtime().side_effect('process_start', context, executable=str(command[0])):
            process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                get_runtime().check_cancelled(context)
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Command exceeded {timeout} seconds")
                # Wait for completion instead of imposing a 150 ms sleep on
                # every tiny Git command. Cancellation is still checked often.
                try:
                    process.wait(timeout=min(.05, max(.001, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
        except BaseException:
            # Only processes created by this invocation, never name-based kills.
            import psutil
            try:
                descendants = psutil.Process(process.pid).children(recursive=True)
            except psutil.Error:
                descendants = []
            for child in reversed(descendants):
                try:
                    child.kill()
                except psutil.Error:
                    pass
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            raise
        output.seek(0, 2)
        size = output.tell()
        if size > max_output_bytes and not allow_truncated:
            raise ValueError('Process output exceeded the configured safe limit; not using a truncated result')
        output.seek(max(0, size - max_output_bytes))
        data = output.read()
        text = data if binary_output else data.decode('utf-8', errors='replace')
        return subprocess.CompletedProcess(command, process.returncode, stdout=text, stderr=b'' if binary_output else '')
