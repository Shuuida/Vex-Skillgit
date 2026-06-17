import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
import tree_sitter_go as tsgo
import tree_sitter_yaml as tsyaml
import tree_sitter_markdown as tsmd
from tree_sitter import Language, Parser, Node

class ASTChunker:
    def __init__(self):
        self.languages = {
            ".py": Language(tspython.language()),
            ".ts": Language(tstypescript.language_typescript()),
            ".js": Language(tstypescript.language_typescript()),
            ".go": Language(tsgo.language()),
            ".yml": Language(tsyaml.language()),
            ".yaml": Language(tsyaml.language()),
            ".md": Language(tsmd.language())
        }
        self.parser = Parser()

    def _get_target_node_types(self, ext: str) -> list[str]:
        if ext == ".py":
            return ["function_definition", "class_definition", "async_function_definition"]
        elif ext in [".ts", ".js"]:
            return ["function_declaration", "class_declaration", "method_definition"]
        elif ext == ".go":
            return ["function_declaration", "method_declaration", "type_declaration"]
        elif ext in [".yml", ".yaml"]:
            return ["block_mapping_pair", "document"]
        elif ext == ".md":
            # Markdown usually structures data in paragraphs, headings, and code blocks
            return ["paragraph", "atx_heading", "fenced_code_block", "list"]
        return []

    def chunk_source_code(self, source_code: str, file_extension: str) -> list[dict]:
        if file_extension not in self.languages:
            raise ValueError(f"Language for extension '{file_extension}' is not supported yet.")

        self.parser.language = self.languages[file_extension]
        tree = self.parser.parse(bytes(source_code, "utf8"))
        target_types = self._get_target_node_types(file_extension)
        chunks = []

        def traverse(node: Node):
            if node.type in target_types:
                chunks.append({
                    "ast_node_type": node.type,
                    "content": node.text.decode("utf8"),
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1
                })
            
            for child in node.children:
                traverse(child)

        traverse(tree.root_node)
        return chunks

ast_chunker = ASTChunker()