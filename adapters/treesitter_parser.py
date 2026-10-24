"""Tree-sitter based parser. Parsing only — this module never executes,
imports, or evaluates any code from the file it is given, only reads its
syntax tree."""
from __future__ import annotations

import tree_sitter_go as tsgo
import tree_sitter_python as tspython
from tree_sitter import Language as TSLanguage
from tree_sitter import Node
from tree_sitter import Parser as TSParser

from core.errors import UnsupportedLanguageError
from core.models import ClassDef, FileParseResult, FunctionDef, ImportDef, Language
from core.ports import Parser as ParserPort

_PY_LANGUAGE = TSLanguage(tspython.language())
_GO_LANGUAGE = TSLanguage(tsgo.language())


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


class TreeSitterParser(ParserPort):
    def __init__(self) -> None:
        self._parsers: dict[Language, TSParser] = {
            Language.PYTHON: TSParser(_PY_LANGUAGE),
            Language.GO: TSParser(_GO_LANGUAGE),
        }

    def supports(self, language: Language) -> bool:
        return language in self._parsers

    def parse_file(self, path: str, source: bytes, language: Language) -> FileParseResult:
        if language not in self._parsers:
            raise UnsupportedLanguageError(f"No grammar registered for {language}")

        tree = self._parsers[language].parse(source)
        root = tree.root_node

        if language == Language.PYTHON:
            functions, classes, imports = self._parse_python(root, source, path)
        else:
            functions, classes, imports = self._parse_go(root, source, path)

        return FileParseResult(
            path=path,
            language=language,
            functions=tuple(functions),
            classes=tuple(classes),
            imports=tuple(imports),
        )

    # ---- Python ----
    def _parse_python(
        self, root: Node, source: bytes, path: str
    ) -> tuple[list[FunctionDef], list[ClassDef], list[ImportDef]]:
        functions: list[FunctionDef] = []
        classes: list[ClassDef] = []
        imports: list[ImportDef] = []

        def walk(node: Node, class_stack: list[str]) -> None:
            if node.type == "class_definition":
                name_node = node.child_by_field_name("name")
                name = _node_text(name_node, source) if name_node else "<anon>"
                qualified = ".".join([*class_stack, name])
                bases: tuple[str, ...] = ()
                superclasses = node.child_by_field_name("superclasses")
                if superclasses:
                    bases = tuple(
                        _node_text(c, source)
                        for c in superclasses.children
                        if c.type == "identifier"
                    )
                classes.append(
                    ClassDef(
                        qualified_name=qualified,
                        name=name,
                        file_path=path,
                        language=Language.PYTHON,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        bases=bases,
                    )
                )
                for child in node.children:
                    walk(child, [*class_stack, name])
                return

            if node.type == "function_definition":
                name_node = node.child_by_field_name("name")
                name = _node_text(name_node, source) if name_node else "<anon>"
                qualified = ".".join([*class_stack, name])
                body_node = node.child_by_field_name("body")
                body_text = _node_text(body_node, source) if body_node else ""
                calls = self._extract_calls_python(node, source)
                docstring = self._extract_docstring_python(body_node, source) if body_node else None
                functions.append(
                    FunctionDef(
                        qualified_name=qualified,
                        name=name,
                        file_path=path,
                        language=Language.PYTHON,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        body_hash=FunctionDef.hash_body(body_text),
                        docstring=docstring,
                        calls=tuple(calls),
                    )
                )
                for child in node.children:
                    walk(child, class_stack)
                return

            if node.type in ("import_statement", "import_from_statement"):
                for child in node.children:
                    if child.type == "dotted_name":
                        imports.append(
                            ImportDef(
                                file_path=path,
                                imported=_node_text(child, source),
                                line=node.start_point[0] + 1,
                            )
                        )

            for child in node.children:
                walk(child, class_stack)

        walk(root, [])
        return functions, classes, imports

    @staticmethod
    def _extract_calls_python(func_node: Node, source: bytes) -> list[str]:
        calls: list[str] = []

        def walk(node: Node) -> None:
            if node.type == "call":
                fn = node.child_by_field_name("function")
                if fn is not None:
                    if fn.type == "identifier":
                        calls.append(_node_text(fn, source))
                    elif fn.type == "attribute":
                        attr = fn.child_by_field_name("attribute")
                        if attr is not None:
                            calls.append(_node_text(attr, source))
            for child in node.children:
                walk(child)

        walk(func_node)
        return calls

    @staticmethod
    def _extract_docstring_python(body_node: Node, source: bytes) -> str | None:
        if not body_node.children:
            return None
        first = body_node.children[0]
        if first.type == "expression_statement" and first.children:
            expr = first.children[0]
            if expr.type == "string":
                text = _node_text(expr, source)
                return text.strip("\"' \n")
        return None

    # ---- Go ----
    def _parse_go(
        self, root: Node, source: bytes, path: str
    ) -> tuple[list[FunctionDef], list[ClassDef], list[ImportDef]]:
        functions: list[FunctionDef] = []
        imports: list[ImportDef] = []

        def walk(node: Node) -> None:
            if node.type in ("function_declaration", "method_declaration"):
                name_node = node.child_by_field_name("name")
                name = _node_text(name_node, source) if name_node else "<anon>"
                receiver_node = node.child_by_field_name("receiver")
                qualified = name
                if receiver_node is not None:
                    rtext = _node_text(receiver_node, source)
                    qualified = f"{rtext.strip('()').split()[-1].lstrip('*')}.{name}"
                body_node = node.child_by_field_name("body")
                body_text = _node_text(body_node, source) if body_node else ""
                calls = self._extract_calls_go(node, source)
                functions.append(
                    FunctionDef(
                        qualified_name=qualified,
                        name=name,
                        file_path=path,
                        language=Language.GO,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        body_hash=FunctionDef.hash_body(body_text),
                        calls=tuple(calls),
                    )
                )
            if node.type == "import_spec":
                path_node = node.child_by_field_name("path")
                if path_node is not None:
                    imports.append(
                        ImportDef(
                            file_path=path,
                            imported=_node_text(path_node, source).strip('"'),
                            line=node.start_point[0] + 1,
                        )
                    )
            for child in node.children:
                walk(child)

        walk(root)
        return functions, [], imports

    @staticmethod
    def _extract_calls_go(func_node: Node, source: bytes) -> list[str]:
        calls: list[str] = []

        def walk(node: Node) -> None:
            if node.type == "call_expression":
                fn = node.child_by_field_name("function")
                if fn is not None:
                    if fn.type == "identifier":
                        calls.append(_node_text(fn, source))
                    elif fn.type == "selector_expression":
                        field = fn.child_by_field_name("field")
                        if field is not None:
                            calls.append(_node_text(field, source))
            for child in node.children:
                walk(child)

        walk(func_node)
        return calls
