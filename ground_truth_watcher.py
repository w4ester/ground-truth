#!/usr/bin/env python3
"""
Ground Truth Watcher - Automatic documentation updater
Monitors file system changes and updates GROUND_TRUTH.md files automatically
"""

import os
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from ground_truth import GroundTruth

__version__ = "1.0.0"

class GroundTruthHandler(FileSystemEventHandler):
    def __init__(self, root_path):
        self.root = Path(root_path).resolve()
        self.ground_truth = GroundTruth(root_path)
        self.pending_updates = set()
        self.last_update = {}
        self.debounce_seconds = 2
        
    def _get_folder_to_update(self, path):
        """Get the folder that needs updating based on the changed file"""
        file_path = Path(path)
        
        # Skip if it's a GROUND_TRUTH.md file itself
        if file_path.name == 'GROUND_TRUTH.md':
            return None
            
        # Get the parent folder
        if file_path.is_file():
            return file_path.parent
        else:
            return file_path
    
    def _should_process(self, folder):
        """Check if we should process this folder (debouncing)"""
        now = time.time()
        last = self.last_update.get(str(folder), 0)
        
        if now - last < self.debounce_seconds:
            return False
            
        self.last_update[str(folder)] = now
        return True
    
    def _update_ground_truth(self, folder):
        """Update GROUND_TRUTH.md for a specific folder"""
        if not self._should_process(folder):
            self.pending_updates.add(folder)
            return
            
        try:
            # Check if folder still exists
            if not folder.exists():
                return
                
            # Update the GROUND_TRUTH.md
            print(f"🔄 Updating: {folder.relative_to(self.root)}/GROUND_TRUTH.md")
            self.ground_truth.create_ground_truth(folder)
            
            # Also update parent folder's GROUND_TRUTH.md
            parent = folder.parent
            if parent != folder and parent.is_dir() and parent >= self.root:
                self.ground_truth.create_ground_truth(parent)
                
        except Exception as e:
            print(f"❌ Error updating {folder}: {e}")
    
    def on_modified(self, event):
        """Handle file modification events"""
        if event.is_directory:
            return
            
        folder = self._get_folder_to_update(event.src_path)
        if folder:
            self._update_ground_truth(folder)
    
    def on_created(self, event):
        """Handle file/folder creation events"""
        folder = self._get_folder_to_update(event.src_path)
        if folder:
            self._update_ground_truth(folder)
            
            # If a new directory was created, also create its GROUND_TRUTH.md
            if event.is_directory:
                new_dir = Path(event.src_path)
                if new_dir.exists():
                    self._update_ground_truth(new_dir)
    
    def on_deleted(self, event):
        """Handle file/folder deletion events"""
        # Update parent folder when something is deleted
        parent = Path(event.src_path).parent
        if parent.exists() and parent >= self.root:
            self._update_ground_truth(parent)
    
    def on_moved(self, event):
        """Handle file/folder move events"""
        # Update both source and destination parent folders
        src_parent = Path(event.src_path).parent
        dest_parent = Path(event.dest_path).parent
        
        if src_parent.exists() and src_parent >= self.root:
            self._update_ground_truth(src_parent)
            
        if dest_parent.exists() and dest_parent >= self.root:
            self._update_ground_truth(dest_parent)
    
    def process_pending(self):
        """Process any pending updates"""
        if self.pending_updates:
            folders = list(self.pending_updates)
            self.pending_updates.clear()
            
            for folder in folders:
                self._update_ground_truth(folder)


def watch(root_path=".", init=False):
    """Start watching for file system changes"""
    root = Path(root_path).resolve()
    
    print(f"🚀 Ground Truth Watcher v{__version__}")
    print(f"📁 Watching: {root}")
    
    # Initialize if requested
    if init:
        print("📝 Initializing GROUND_TRUTH.md files...")
        gt = GroundTruth(root_path)
        gt.init_all()
        print()
    
    # Set up the watcher
    event_handler = GroundTruthHandler(root_path)
    observer = Observer()
    observer.schedule(event_handler, str(root), recursive=True)
    
    # Start watching
    observer.start()
    print("👀 Watching for changes... (Press Ctrl+C to stop)")
    print("📊 Updates happen 2 seconds after changes (debounced)")
    print()
    
    try:
        while True:
            time.sleep(5)
            # Process any pending updates
            event_handler.process_pending()
    except KeyboardInterrupt:
        print("\n⏹️  Stopping watcher...")
        observer.stop()
    
    observer.join()
    print("✅ Watcher stopped")


def main():
    parser = argparse.ArgumentParser(
        description='Ground Truth Watcher - Automatic documentation updater'
    )
    parser.add_argument(
        'path',
        nargs='?',
        default='.',
        help='Path to watch (default: current directory)'
    )
    parser.add_argument(
        '--init',
        action='store_true',
        help='Initialize all GROUND_TRUTH.md files before watching'
    )
    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {__version__}'
    )
    
    args = parser.parse_args()
    
    # Check if path exists
    path = Path(args.path)
    if not path.exists():
        print(f"❌ Error: Path does not exist: {path}")
        sys.exit(1)
    
    if not path.is_dir():
        print(f"❌ Error: Path is not a directory: {path}")
        sys.exit(1)
    
    # Start watching
    watch(args.path, args.init)


if __name__ == '__main__':
    main()