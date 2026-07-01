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

    def chunk_source_code(self, source_code: str, file_extension: str) -> list[dict]:
        if file_extension not in self.languages:
            raise ValueError(f"Language for extension '{file_extension}' is not supported yet.")

        parser = Parser()
        parser.language = self.languages[file_extension]
        tree = parser.parse(bytes(source_code, "utf8"))
        target_types = self._get_target_node_types(file_extension)
        chunks = []

        def traverse(node: Node):
            if node.type in target_types:
                chunks.append({
                    "ast_node_type": node.type,
                    "content": node.text.decode("utf8"),
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "dependencies": self._extract_dependencies(node)
                })
            
            for child in node.children:
                traverse(child)

        traverse(tree.root_node)
        return chunks

ast_chunker = ASTChunker()