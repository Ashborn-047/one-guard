import subprocess
import sys
import time
import signal

processes = []

def signal_handler(sig, frame):
    print("\n[OneGuard] Stopping dashboard services...")
    for p in processes:
        try:
            p.terminate()
            p.wait(timeout=2)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    print("[OneGuard] Both servers halted successfully. Goodbye!")
    sys.exit(0)

# Register Ctrl+C handler
signal.signal(signal.SIGINT, signal_handler)

def main():
    print("====================================================")
    print("   OneGuard Trading Command Center Launcher         ")
    print("====================================================")
    
    try:
        # Start Python FastAPI backend
        print("[OneGuard] Starting FastAPI Backend on port 8000...")
        # Use python executable from the active virtual env if possible
        python_bin = sys.executable
        backend_cmd = [python_bin, "-m", "uvicorn", "dashboard.api:app", "--port", "8000"]
        p_backend = subprocess.Popen(backend_cmd)
        processes.append(p_backend)
        
        # Short sleep to let the backend bind to the port
        time.sleep(2)
        
        # Start Vite React dev server
        print("[OneGuard] Starting Vite React Frontend on port 5173...")
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        p_frontend = subprocess.Popen([npm_cmd, "run", "dev"], cwd="dashboard/frontend")
        processes.append(p_frontend)
        
        print("\n[OneGuard] Dashboard is active! Access frontend at http://localhost:5173")
        print("[OneGuard] Press Ctrl+C in this terminal window to stop both servers.")
        print("----------------------------------------------------\n")
        
        # Keep launcher script running
        while True:
            # Check if any process terminated unexpectedly
            for p in processes:
                if p.poll() is not None:
                    print(f"\n[OneGuard] Process {p.args} exited unexpectedly with code {p.returncode}.")
                    signal_handler(None, None)
            time.sleep(1)
            
    except KeyboardInterrupt:
        signal_handler(None, None)
    except Exception as e:
        print(f"\n[OneGuard] Launch error: {e}")
        signal_handler(None, None)

if __name__ == "__main__":
    main()
