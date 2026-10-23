from adapters.treesitter_parser import TreeSitterParser
from core.models import Language


def test_parses_python_functions_and_classes():
    parser = TreeSitterParser()
    source = b'''
class Greeter:
    """Greets people."""

    def greet(self, name):
        """Return a greeting."""
        msg = compute(name)
        return self.format(msg)

    def format(self, msg):
        return msg.upper()
'''
    result = parser.parse_file("main.py", source, Language.PYTHON)

    assert result.path == "main.py"
    assert len(result.classes) == 1
    assert result.classes[0].name == "Greeter"

    names = {f.qualified_name for f in result.functions}
    assert "Greeter.greet" in names
    assert "Greeter.format" in names

    greet = next(f for f in result.functions if f.qualified_name == "Greeter.greet")
    assert "compute" in greet.calls
    assert "format" in greet.calls
    assert greet.docstring == "Return a greeting."


def test_parses_python_imports():
    parser = TreeSitterParser()
    source = b"import os\nfrom pkg.helper import compute\n"
    result = parser.parse_file("main.py", source, Language.PYTHON)
    imported = {i.imported for i in result.imports}
    assert "os" in imported


def test_body_hash_stable_across_whitespace_only_changes():
    parser = TreeSitterParser()
    src_a = b"def f():\n    return 1\n"
    src_b = b"def f():\n    return 1   \n"  # trailing whitespace only
    fn_a = parser.parse_file("a.py", src_a, Language.PYTHON).functions[0]
    fn_b = parser.parse_file("b.py", src_b, Language.PYTHON).functions[0]
    assert fn_a.body_hash == fn_b.body_hash


def test_body_hash_changes_on_real_change():
    parser = TreeSitterParser()
    src_a = b"def f():\n    return 1\n"
    src_b = b"def f():\n    return 2\n"
    fn_a = parser.parse_file("a.py", src_a, Language.PYTHON).functions[0]
    fn_b = parser.parse_file("b.py", src_b, Language.PYTHON).functions[0]
    assert fn_a.body_hash != fn_b.body_hash


def test_parses_go_functions_and_receiver():
    parser = TreeSitterParser()
    source = b'''
package main

func (g *Greeter) Greet(name string) string {
	msg := compute(name)
	return msg
}

func compute(name string) string {
	return "hello " + name
}
'''
    result = parser.parse_file("main.go", source, Language.GO)
    names = {f.qualified_name for f in result.functions}
    assert "Greeter.Greet" in names
    assert "compute" in names

    greet = next(f for f in result.functions if f.qualified_name == "Greeter.Greet")
    assert "compute" in greet.calls


def test_malformed_python_does_not_raise():
    """Tree-sitter is error-tolerant by design: malformed source must not
    crash the parser. It may simply yield fewer/no extracted functions."""
    parser = TreeSitterParser()
    source = b"def broken(:\n    this is not valid python at all @@@\n"
    result = parser.parse_file("broken.py", source, Language.PYTHON)
    assert result.path == "broken.py"  # did not raise
