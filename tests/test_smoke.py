def test_agent_package_imports():
    import magic_agent
    assert magic_agent.__doc__ is not None


def test_scanner_dependency_is_importable():
    from magic_scanner.scan import scan_symbols  # editable path dep resolves
    assert callable(scan_symbols)
