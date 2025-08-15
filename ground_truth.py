#!/usr/bin/env python3
"""
Ground Truth Documentation System
Auto-generates and maintains GROUND_TRUTH.md files for every folder in your project
Tracks dependencies, exports, TODOs, environment variables, and API endpoints
"""

import os
import re
import ast
import sys
import subprocess
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Set, List, Dict, Optional, Tuple
import fnmatch

__version__ = "2.0.0"
__author__ = "Ground Truth Contributors"

class GroundTruth:
    def __init__(self, root_path: str = "."):
        self.root = Path(root_path).resolve()
        self.gitignore_patterns = self._load_gitignore_patterns()
        
    def _load_gitignore_patterns(self) -> Set[str]:
        """Load patterns from .gitignore files"""
        patterns = set()
        
        # Add default patterns that should always be ignored
        default_patterns = {
            '.git', '__pycache__', '*.pyc', '*.pyo', '*.pyd',
            'node_modules', 'venv', '.venv', 'env', '.env',
            'dist', 'build', '*.egg-info', '.pytest_cache',
            '.coverage', 'htmlcov', '.tox', '.mypy_cache',
            '*.log', '*.sqlite', '*.db', '.DS_Store', 'Thumbs.db'
        }
        patterns.update(default_patterns)
        
        # Load from .gitignore if it exists
        gitignore_path = self.root / '.gitignore'
        if gitignore_path.exists():
            with open(gitignore_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        patterns.add(line.rstrip('/'))
        
        return patterns
    
    def _should_ignore(self, path: Path) -> bool:
        """Check if a path should be ignored based on gitignore patterns"""
        try:
            rel_path = path.relative_to(self.root)
        except ValueError:
            return True
            
        path_str = str(rel_path)
        
        # Check each component of the path
        parts = path_str.split(os.sep)
        for i in range(len(parts)):
            partial = os.sep.join(parts[:i+1])
            
            for pattern in self.gitignore_patterns:
                pattern_clean = pattern.rstrip('/')
                
                if fnmatch.fnmatch(partial, pattern_clean):
                    return True
                
                for j in range(i + 1):
                    parent_path = os.sep.join(parts[:j+1])
                    if parent_path == pattern_clean or fnmatch.fnmatch(parent_path, pattern_clean):
                        return True
                
                if pattern.startswith('**/'):
                    if fnmatch.fnmatch(partial, pattern[3:]):
                        return True
                
                if '/' not in pattern_clean and '*' not in pattern_clean:
                    for part in parts[:i+1]:
                        if part == pattern_clean:
                            return True
        
        return False
    
    def _run_git_command(self, cmd: List[str]) -> Optional[str]:
        """Run a git command and return output"""
        try:
            result = subprocess.run(
                ['git'] + cmd,
                cwd=self.root,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None
    
    def _get_git_changes(self, folder: Path) -> List[Dict]:
        """Get recent git changes for a folder"""
        changes = []
        rel_folder = str(folder.relative_to(self.root))
        
        log_output = self._run_git_command([
            'log', '--pretty=format:%H|%ai|%s', 
            '-n', '10',
            '--', rel_folder
        ])
        
        if log_output:
            for line in log_output.split('\n'):
                if line:
                    parts = line.split('|', 2)
                    if len(parts) == 3:
                        commit_hash, date, message = parts
                        changes.append({
                            'date': date.split()[0],
                            'message': message[:100],
                            'hash': commit_hash[:7]
                        })
        
        return changes
    
    def _analyze_python_file(self, file_path: Path) -> Dict:
        """Analyze a Python file for imports, exports, TODOs, etc."""
        analysis = {
            'imports': [],
            'imports_from': [],
            'exports': [],
            'todos': [],
            'env_vars': [],
            'api_endpoints': []
        }
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Parse AST for imports and exports
            try:
                tree = ast.parse(content)
                
                for node in ast.walk(tree):
                    # Detect imports
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            analysis['imports'].append(alias.name)
                    
                    elif isinstance(node, ast.ImportFrom):
                        module = node.module or ''
                        level = node.level
                        
                        # Convert relative imports to paths
                        if level > 0:
                            rel_path = '../' * (level - 1) if level > 1 else './'
                            module_path = f"{rel_path}{module.replace('.', '/')}" if module else rel_path
                        else:
                            module_path = module
                            
                        for alias in node.names:
                            if alias.name != '*':
                                analysis['imports_from'].append(f"{module_path}.{alias.name}")
                            else:
                                analysis['imports_from'].append(f"{module_path}.*")
                    
                    # Detect function and class definitions (exports) AND API endpoints
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if not node.name.startswith('_'):  # Skip private functions
                            params = ', '.join(arg.arg for arg in node.args.args)
                            analysis['exports'].append(f"def {node.name}({params})")
                        
                        # Check decorators for API endpoints
                        for decorator in node.decorator_list:
                            # Check if it's an attribute (like router.get)
                            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                                obj_name = decorator.func.value.id if isinstance(decorator.func.value, ast.Name) else None
                                method_name = decorator.func.attr
                                
                                # Check for router/app methods
                                if obj_name in ['router', 'app', 'bp'] and method_name in ['get', 'post', 'put', 'delete', 'patch', 'route']:
                                    # Get the path from the first argument
                                    if decorator.args and isinstance(decorator.args[0], ast.Constant):
                                        path = decorator.args[0].value
                                        method = method_name.upper() if method_name != 'route' else 'GET'
                                        analysis['api_endpoints'].append(f"{method} {path} → {node.name}()")
                    
                    elif isinstance(node, ast.ClassDef):
                        if not node.name.startswith('_'):  # Skip private classes
                            analysis['exports'].append(f"class {node.name}")
            
            except SyntaxError:
                pass  # File might have syntax errors
            
            # Find TODOs and FIXMEs with line numbers
            lines = content.split('\n')
            for i, line in enumerate(lines, 1):
                if 'TODO' in line or 'FIXME' in line or 'HACK' in line or 'XXX' in line:
                    # Extract the comment part
                    comment_match = re.search(r'(#|//|/\*)\s*(TODO|FIXME|HACK|XXX)[:\s]*(.*)', line)
                    if comment_match:
                        tag = comment_match.group(2)
                        message = comment_match.group(3).strip()
                        analysis['todos'].append(f"line {i}: {tag}: {message[:60]}")
            
            # Find environment variable usage
            env_patterns = [
                r'os\.environ\.get\(["\']([^"\']+)',
                r'os\.environ\[["\']([^"\']+)',
                r'os\.getenv\(["\']([^"\']+)',
                r'config\(["\']([^"\']+)',
                r'settings\.([A-Z_]+)',
                r'env\.([A-Z_]+)',
            ]
            
            for pattern in env_patterns:
                matches = re.findall(pattern, content)
                for match in matches:
                    if match.upper() == match or '_' in match:  # Likely env var
                        if match not in analysis['env_vars']:
                            analysis['env_vars'].append(match)
            
        except Exception as e:
            pass  # File might not be readable
        
        return analysis
    
    def _analyze_javascript_file(self, file_path: Path) -> Dict:
        """Analyze a JavaScript/TypeScript file for imports, exports, TODOs, etc."""
        analysis = {
            'imports': [],
            'imports_from': [],
            'exports': [],
            'todos': [],
            'env_vars': [],
            'api_endpoints': []
        }
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Find imports
            import_patterns = [
                r'import\s+(?:{[^}]+}|\*\s+as\s+\w+|\w+)\s+from\s+["\']([^"\']+)',
                r'const\s+\w+\s*=\s*require\(["\']([^"\']+)',
                r'import\(["\']([^"\']+)'
            ]
            
            for pattern in import_patterns:
                matches = re.findall(pattern, content)
                for match in matches:
                    analysis['imports_from'].append(match)
            
            # Find exports
            export_patterns = [
                r'export\s+(?:default\s+)?(?:class|function|const|let|var)\s+(\w+)',
                r'export\s+{([^}]+)}',
                r'module\.exports\s*=\s*{([^}]+)}'
            ]
            
            for pattern in export_patterns:
                matches = re.findall(pattern, content)
                for match in matches:
                    if ',' in match:
                        # Multiple exports
                        for exp in match.split(','):
                            exp = exp.strip()
                            if exp:
                                analysis['exports'].append(exp)
                    else:
                        analysis['exports'].append(match)
            
            # Find API endpoints (common patterns)
            endpoint_patterns = [
                r'fetch\(["\']([^"\']+)',
                r'axios\.(get|post|put|delete|patch)\(["\']([^"\']+)',
                r'app\.(get|post|put|delete|patch)\(["\']([^"\']+)',
                r'router\.(get|post|put|delete|patch)\(["\']([^"\']+)',
                r'@(Get|Post|Put|Delete|Patch)\(["\']([^"\']+)'  # NestJS
            ]
            
            for pattern in endpoint_patterns:
                matches = re.findall(pattern, content)
                for match in matches:
                    if isinstance(match, tuple):
                        if len(match) == 2:
                            method, path = match
                            analysis['api_endpoints'].append(f"{method.upper()} {path}")
                    else:
                        analysis['api_endpoints'].append(match)
            
            # Find TODOs and FIXMEs
            lines = content.split('\n')
            for i, line in enumerate(lines, 1):
                if 'TODO' in line or 'FIXME' in line or 'HACK' in line:
                    comment_match = re.search(r'(//|/\*)\s*(TODO|FIXME|HACK)[:\s]*(.*)', line)
                    if comment_match:
                        tag = comment_match.group(2)
                        message = comment_match.group(3).strip()
                        analysis['todos'].append(f"line {i}: {tag}: {message[:60]}")
            
            # Find environment variable usage
            env_patterns = [
                r'process\.env\.([A-Z_]+)',
                r'import\.meta\.env\.([A-Z_]+)',
                r'env\.([A-Z_]+)'
            ]
            
            for pattern in env_patterns:
                matches = re.findall(pattern, content)
                for match in matches:
                    if match not in analysis['env_vars']:
                        analysis['env_vars'].append(match)
            
        except Exception:
            pass
        
        return analysis
    
    def _analyze_folder(self, folder: Path) -> Dict:
        """Analyze all files in a folder for dependencies, exports, etc."""
        folder_analysis = {
            'all_imports': set(),
            'all_exports': set(),
            'all_todos': [],
            'all_env_vars': set(),
            'all_api_endpoints': set(),
            'imported_by': []
        }
        
        # Analyze each file
        for file_path in folder.glob('*'):
            if file_path.is_file() and not self._should_ignore(file_path):
                analysis = None
                
                if file_path.suffix in ['.py']:
                    analysis = self._analyze_python_file(file_path)
                elif file_path.suffix in ['.js', '.jsx', '.ts', '.tsx', '.mjs']:
                    analysis = self._analyze_javascript_file(file_path)
                
                if analysis:
                    # Aggregate imports
                    for imp in analysis['imports_from']:
                        folder_analysis['all_imports'].add(imp)
                    
                    # Aggregate exports with filename
                    for exp in analysis['exports']:
                        folder_analysis['all_exports'].add(f"{file_path.name}: {exp}")
                    
                    # Aggregate TODOs with filename
                    for todo in analysis['todos']:
                        folder_analysis['all_todos'].append(f"{file_path.name} {todo}")
                    
                    # Aggregate env vars
                    folder_analysis['all_env_vars'].update(analysis['env_vars'])
                    
                    # Aggregate API endpoints
                    folder_analysis['all_api_endpoints'].update(analysis['api_endpoints'])
        
        return folder_analysis
    
    def _get_folder_info(self, folder: Path) -> Dict:
        """Get information about a folder"""
        info = {
            'files': [],
            'purpose': self._infer_folder_purpose(folder.name),
            'size': 0,
            'last_modified': None
        }
        
        # List files (not subdirectories)
        for item in folder.iterdir():
            if item.is_file() and not self._should_ignore(item):
                stat = item.stat()
                rel_path = item.relative_to(folder)
                
                info['files'].append({
                    'name': str(rel_path),
                    'size': stat.st_size,
                    'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    'purpose': self._infer_file_purpose(item.name)
                })
                info['size'] += stat.st_size
                
                if info['last_modified'] is None or stat.st_mtime > info['last_modified']:
                    info['last_modified'] = stat.st_mtime
        
        # Sort files by name
        info['files'].sort(key=lambda x: x['name'])
        
        return info
    
    def _infer_folder_purpose(self, folder_name: str) -> str:
        """Infer the purpose of a folder from its name"""
        purposes = {
            'src': 'Source code',
            'tests': 'Test files',
            'test': 'Test files',
            'docs': 'Documentation',
            'scripts': 'Utility scripts',
            'config': 'Configuration files',
            'public': 'Public static files',
            'static': 'Static files',
            'templates': 'Template files',
            'migrations': 'Database migrations',
            'components': 'UI components',
            'api': 'API implementation',
            'endpoints': 'API endpoints',
            'models': 'Data models',
            'schemas': 'Data schemas',
            'services': 'Business logic services',
            'utils': 'Utility functions',
            'lib': 'Library code',
            'routes': 'Application routes',
            'assets': 'Static assets',
            'crud': 'Database CRUD operations',
            'core': 'Core functionality',
            'auth': 'Authentication logic'
        }
        
        folder_lower = folder_name.lower()
        return purposes.get(folder_lower, 'Project files')
    
    def _infer_file_purpose(self, filename: str) -> str:
        """Infer the purpose of a file from its name and extension"""
        name_lower = filename.lower()
        
        # Check full filename first
        if name_lower == 'readme.md':
            return 'Documentation'
        if name_lower == 'dockerfile':
            return 'Docker configuration'
        if name_lower == 'package.json':
            return 'Node.js package manifest'
        if name_lower == 'requirements.txt':
            return 'Python dependencies'
        if name_lower == 'makefile':
            return 'Build automation'
        
        # Check by extension
        ext = Path(filename).suffix.lower()
        extensions = {
            '.py': 'Python module',
            '.js': 'JavaScript module',
            '.ts': 'TypeScript module',
            '.jsx': 'React component',
            '.tsx': 'React TypeScript component',
            '.svelte': 'Svelte component',
            '.vue': 'Vue component',
            '.html': 'HTML template',
            '.css': 'Stylesheet',
            '.json': 'JSON data',
            '.yaml': 'YAML configuration',
            '.yml': 'YAML configuration',
            '.toml': 'TOML configuration',
            '.sql': 'SQL script',
            '.sh': 'Shell script',
            '.md': 'Documentation',
            '.txt': 'Text file',
            '.env': 'Environment variables'
        }
        
        return extensions.get(ext, 'Unknown purpose')
    
    def create_ground_truth(self, folder: Path) -> None:
        """Create or update GROUND_TRUTH.md for a folder with enhanced features"""
        if self._should_ignore(folder):
            return
            
        ground_truth_path = folder / 'GROUND_TRUTH.md'
        info = self._get_folder_info(folder)
        changes = self._get_git_changes(folder)
        
        # Run enhanced analysis
        analysis = self._analyze_folder(folder)
        
        # Format file size
        def format_size(size):
            for unit in ['B', 'KB', 'MB', 'GB']:
                if size < 1024.0:
                    return f"{size:.1f}{unit}"
                size /= 1024.0
            return f"{size:.1f}TB"
        
        # Build the markdown content
        rel_path = folder.relative_to(self.root)
        content = [
            f"# GROUND_TRUTH.md for {rel_path}",
            f"Last Updated: {datetime.now().isoformat()}",
            "Auto-generated by ground_truth.py",
            "",
            "## 📁 Purpose",
            info['purpose'],
            "",
            f"## 📄 Files ({len(info['files'])} files)",
        ]
        
        if info['files']:
            for file in info['files']:
                size_str = format_size(file['size'])
                mod_date = file['modified'].split('T')[0]
                content.append(f"- `{file['name']}` - {file['purpose']} (Modified: {mod_date}, Size: {size_str})")
        else:
            content.append("- No tracked files in this directory")
        
        # Add exports section if any
        if analysis['all_exports']:
            content.extend([
                "",
                "## 📦 Exports",
            ])
            for export in sorted(analysis['all_exports'])[:15]:  # Limit to 15 for readability
                content.append(f"- {export}")
            if len(analysis['all_exports']) > 15:
                content.append(f"- ... and {len(analysis['all_exports']) - 15} more")
        
        # Add dependencies section
        if analysis['all_imports'] or analysis['imported_by']:
            content.extend([
                "",
                "## 🔗 Dependencies",
            ])
            
            if analysis['all_imports']:
                content.append("### This folder imports:")
                # Group and clean imports
                local_imports = [imp for imp in analysis['all_imports'] if imp.startswith('.')]
                external_imports = [imp for imp in analysis['all_imports'] if not imp.startswith('.')]
                
                for imp in sorted(local_imports)[:10]:
                    content.append(f"- {imp}")
                    
                if external_imports:
                    content.append("### External imports:")
                    for imp in sorted(external_imports)[:10]:
                        content.append(f"- {imp}")
            
            if analysis['imported_by']:
                content.append("### Imported by:")
                for imp in sorted(set(analysis['imported_by']))[:10]:
                    content.append(f"- {imp}")
        
        # Add TODOs section if any
        if analysis['all_todos']:
            content.extend([
                "",
                "## 📝 TODOs & FIXMEs",
            ])
            for todo in analysis['all_todos'][:10]:  # Limit to 10
                content.append(f"- {todo}")
            if len(analysis['all_todos']) > 10:
                content.append(f"- ... and {len(analysis['all_todos']) - 10} more")
        
        # Add environment variables section if any
        if analysis['all_env_vars']:
            content.extend([
                "",
                "## 🔐 Environment Variables",
            ])
            for env_var in sorted(analysis['all_env_vars'])[:15]:
                content.append(f"- {env_var}")
        
        # Add API endpoints section if any
        if analysis['all_api_endpoints']:
            content.extend([
                "",
                "## 🌐 API Endpoints",
            ])
            for endpoint in sorted(analysis['all_api_endpoints'])[:15]:
                content.append(f"- {endpoint}")
            if len(analysis['all_api_endpoints']) > 15:
                content.append(f"- ... and {len(analysis['all_api_endpoints']) - 15} more")
        
        # Add git changes
        content.extend([
            "",
            "## 🔄 Recent Git Changes",
        ])
        
        if changes:
            for change in changes[:5]:
                content.append(f"- [{change['date']}] {change['message']} ({change['hash']})")
        else:
            content.append("- No git history for this folder yet")
        
        content.extend([
            "",
            "## ⚠️ Critical Information",
            "<!-- Add warnings about what breaks if changed -->",
            "- This folder is tracked by ground truth system",
            "- Changes are automatically logged via git hooks",
            "- Manual edits to this section are preserved",
            "",
            "## 🤖 LLM Instructions",
            "**BEFORE modifying ANY file in this folder:**",
            "1. READ this GROUND_TRUTH.md completely",
            "2. CHECK parent folder's GROUND_TRUTH.md",
            "3. VERIFY no dependencies will break",
            "4. UPDATE will happen automatically via git hooks",
            "",
            "## 📊 Folder Statistics",
            f"- Total files: {len(info['files'])}",
            f"- Total size: {format_size(info['size'])}",
            f"- Last scan: {datetime.now().isoformat()}",
            ""
        ])
        
        # Preserve any custom sections if file exists
        if ground_truth_path.exists():
            with open(ground_truth_path, 'r') as f:
                old_content = f.read()
                
            # Look for custom critical information
            if "## ⚠️ Critical Information" in old_content:
                start = old_content.find("## ⚠️ Critical Information")
                end = old_content.find("\n## ", start + 1)
                if end == -1:
                    end = old_content.find("\n## 🤖", start)
                
                if end > start:
                    custom_section = old_content[start:end].strip()
                    # Replace the generic critical section with custom one
                    crit_index = content.index("## ⚠️ Critical Information")
                    # Remove lines until next section
                    while crit_index + 1 < len(content) and not content[crit_index + 1].startswith("## "):
                        content.pop(crit_index + 1)
                    # Insert custom content
                    for line in custom_section.split('\n')[1:]:  # Skip the header
                        crit_index += 1
                        content.insert(crit_index, line)
        
        # Write the file
        with open(ground_truth_path, 'w') as f:
            f.write('\n'.join(content))
        
        print(f"✅ Updated: {ground_truth_path}")
    
    def init_all(self) -> None:
        """Initialize GROUND_TRUTH.md files for all folders"""
        print(f"🚀 Initializing Ground Truth for: {self.root}")
        print("📊 Analyzing dependencies, exports, TODOs, env vars, and API endpoints...")
        
        count = 0
        for folder in self.root.rglob('*'):
            if folder.is_dir() and not self._should_ignore(folder):
                self.create_ground_truth(folder)
                count += 1
        
        # Also create for root
        self.create_ground_truth(self.root)
        count += 1
        
        print(f"\n✨ Created {count} GROUND_TRUTH.md files")
        print("📝 Files in .gitignore directories were skipped")
        print("🔍 Dependencies and exports analyzed")
        print("📌 TODOs and environment variables extracted")


def main():
    parser = argparse.ArgumentParser(description='Ground Truth Documentation System')
    parser.add_argument('command', choices=['init', 'update'],
                       help='Command to run')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    
    args = parser.parse_args()
    
    gt = GroundTruth()
    
    if args.command in ['init', 'update']:
        gt.init_all()


if __name__ == '__main__':
    main()