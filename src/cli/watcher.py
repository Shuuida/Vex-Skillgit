import os
import time
import shutil
import uuid
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from src.tasks import process_ingestion_task
from src.config import TEMP_UPLOAD_DIR

SUPPORTED_EXTENSIONS = (".py", ".ts", ".js", ".go", ".yml", ".yaml", ".md")
IGNORE_DIRS = {".venv", "venv", "node_modules", ".git", ".vex", "__pycache__", "temp_uploads", ".idea", ".vscode", ".pytest_cache", ".ruff_cache", ".mypy_cache"}

class VexSyncHandler(FileSystemEventHandler):
    def __init__(self, tenant_id, skill_id):
        self.tenant_id = tenant_id
        self.skill_id = skill_id

    def _infer_intent(self, file_path: str) -> str:
        """Infer the semantic intent based on the modified file"""
        path_lower = file_path.lower()
        
        if path_lower.endswith(".md"):
            return "docs"
        elif "test" in path_lower or "spec" in path_lower:
            return "test"
        elif "requirements.txt" in path_lower or "package.json" in path_lower or "go.mod" in path_lower:
            return "chore"
        else:
            return "fix"

    def on_modified(self, event):
        if event.is_directory or not event.src_path.endswith(SUPPORTED_EXTENSIONS):
            return
            
        normalized_path = os.path.normpath(event.src_path)
        path_parts = set(normalized_path.split(os.sep))
        
        if IGNORE_DIRS.intersection(path_parts) or os.path.basename(TEMP_UPLOAD_DIR) in path_parts:
            return
            
        intent = self._infer_intent(event.src_path)
        os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)
        filename = os.path.basename(event.src_path)
        
        safe_temp_path = os.path.join(TEMP_UPLOAD_DIR, f"watch_{uuid.uuid4().hex[:8]}_{filename}")
        
        try:
            shutil.copy2(event.src_path, safe_temp_path)
        except Exception as e:
            print(f"[Vex Watcher] Error cloning the file {event.src_path}: {e}")
            return
            
        print(f"[Vex Watcher] File saved: {event.src_path} -> Ingesting safe copy as: '{intent}'")
        
        process_ingestion_task(
            temp_file_path=safe_temp_path,
            tenant_id=self.tenant_id,
            skill_id=self.skill_id,
            commit_type=intent
        )

def start_watch(path=".", tenant_id="local_dev", skill_id="workspace"):
    event_handler = VexSyncHandler(tenant_id, skill_id)
    observer = Observer()
    observer.schedule(event_handler, path, recursive=True)
    observer.start()
    
    print(f"Vex Local Watcher active in: {os.path.abspath(path)}")
    print("Saving real-time context. Press Ctrl+C to stop.")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\nWatcher has been stopped.")
    observer.join()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Vex Local Sync Watcher")
    parser.add_argument("--path", default=".", help="Repository path to monitor")
    parser.add_argument("--skill", default="local_workspace", help="Skill ID for Vex")
    args = parser.parse_args()
    
    start_watch(path=args.path, skill_id=args.skill)