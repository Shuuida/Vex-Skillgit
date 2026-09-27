import os
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from src.tasks import process_ingestion_task

SUPPORTED_EXTENSIONS = (".py", ".ts", ".js", ".go", ".yml", ".yaml", ".md")

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
            return "chore" # Maintenance tasks/dependencies
        else:
            # If it's a source code file, by default treat it as a fix/update
            # so the database replaces the context (Upsert) instead of duplicating it.
            return "fix"

    def on_modified(self, event):
        if event.is_directory or not event.src_path.endswith(SUPPORTED_EXTENSIONS):
            return
            
        intent = self._infer_intent(event.src_path)
        print(f"[Vex Watcher] File saved: {event.src_path} -> Inferred as: '{intent}'")
        
        process_ingestion_task(
            temp_file_path=event.src_path,
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