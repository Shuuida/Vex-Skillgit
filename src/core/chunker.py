import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
import tree_sitter_javascript as tsjavascript
import tree_sitter_go as tsgo
import tree_sitter_yaml as tsyaml
import tree_sitter_markdown as tsmd
from tree_sitter import Language, Parser, Node

class ASTChunker:
    def __init__(self):
        self.languages = {
            ".py": Language(tspython.language()),
            ".ts": Language(tstypescript.language_typescript()),
            ".tsx": Language(tstypescript.language_tsx()),
            ".js": Language(tsjavascript.language()),
            ".jsx": Language(tstypescript.language_tsx()),
            ".go": Language(tsgo.language()),
            ".yml": Language(tsyaml.language()),
            ".yaml": Language(tsyaml.language()),
            ".md": Language(tsmd.language())
        }

    def _get_target_node_types(self, ext: str) -> list[str]:
        if ext == ".py":
            return ["function_definition", "class_definition", "async_function_definition"]
        elif ext in [".ts", ".js", ".tsx", ".jsx"]:
            return ["function_declaration", "class_declaration", "method_definition"]
        elif ext == ".go":
            return ["function_declaration", "method_declaration", "type_declaration"]
        elif ext in [".yml", ".yaml"]:
            return ["block_mapping_pair", "document"]
        elif ext == ".md":
            # Markdown usually structures data in paragraphs, headings, and code blocks
            return ["paragraph", "atx_heading", "fenced_code_block", "list"]
        return []

    def _extract_dependencies(self, node: Node) -> list[str]:
        """Extract function calls to map dependencies."""
        deps = set()
        def walk(n: Node):
            # Identifies generic function calls in Python, JS, TS, Go
            if "call" in n.type:
                for child in n.children:
                    if child.type == "identifier":
                        deps.add(child.text.decode("utf8"))
                    elif child.type == "attribute":
                        # To extract methods: object.method() -> extracts "method"
                        for sub in child.children:
                            if sub.type == "identifier" and sub != child.children[0]:
                                deps.add(sub.text.decode("utf8"))
            for c in n.children:
                walk(c)
        walk(node)
        return list(deps)

    def _subchunk_by_lines(self, content: str, chunk_size: int, overlap: int) -> list[str]:
        """Perform sub-chunking respecting line breaks to avoid breaking code."""
        lines = content.splitlines(keepends=True)
        chunks = []
        current_chunk = ""
        
        for line in lines:
            # If adding this line exceeds the limit (and already has something saved)
            if len(current_chunk) + len(line) > chunk_size and current_chunk:
                chunks.append(current_chunk)
                
                # Go back for the overlap
                overlap_buffer = ""
                for prev_line in reversed(current_chunk.splitlines(keepends=True)):
                    if len(overlap_buffer) + len(prev_line) <= overlap:
                        overlap_buffer = prev_line + overlap_buffer
                    else:
                        break
                        
                current_chunk = overlap_buffer + line
            else:
                current_chunk += line
                
        if current_chunk:
            chunks.append(current_chunk)
            
        return chunks

    def chunk_source_code(self, source_code: str, file_extension: str, chunk_size: int = 1000, overlap: int = 200) -> list[dict]:
        """
        Extracts semantic chunks using Tree-sitter AST, and applies memory tuning 
        to split oversized nodes while respecting the overlap.
        """
        if file_extension not in self.languages:
            raise ValueError(f"Language for extension '{file_extension}' is not supported yet.")

        parser = Parser()
        parser.language = self.languages[file_extension]
        tree = parser.parse(bytes(source_code, "utf8"))
        target_types = self._get_target_node_types(file_extension)
        
        extracted_nodes = []

        def traverse(node: Node):
            if node.type in target_types:
                extracted_nodes.append({
                    "ast_node_type": node.type,
                    "content": node.text.decode("utf8"),
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "dependencies": self._extract_dependencies(node)
                })
            
            for child in node.children:
                traverse(child)

        traverse(tree.root_node)

        final_chunks = []
        for node_data in extracted_nodes:
            content = node_data["content"]
            
            if len(content) > chunk_size:
                sub_contents = self._subchunk_by_lines(content, chunk_size, overlap)
                
                for piece in sub_contents:
                    final_chunks.append({
                        "ast_node_type": node_data["ast_node_type"],
                        "content": piece,
                        "start_line": node_data["start_line"], # Approximate for the sub-chunk
                        "end_line": node_data["end_line"],     
                        "dependencies": node_data["dependencies"]
                    })
            else:
                final_chunks.append(node_data)
                
        return final_chunks

ast_chunker = ASTChunker()