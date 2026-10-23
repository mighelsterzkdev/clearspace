"""Fixed native cleanup providers; renderer input can never add commands."""

NATIVE_ACTION_IDS = frozenset({
    "windows_components", "delivery_optimization", "run_history",
    "explorer_search_history", "recent_document_history",
})


def create_provider(identifier):
    if identifier in {"windows_components", "delivery_optimization"}:
        from .maintenance import WindowsComponentCleanup, WindowsDeliveryOptimization
        return WindowsComponentCleanup() if identifier == "windows_components" else WindowsDeliveryOptimization()
    if identifier in NATIVE_ACTION_IDS:
        from .history import history_providers
        return history_providers()[identifier]
    raise ValueError("Unsupported native cleanup action.")


def categories():
    from .engine import Category
    return [
        Category("windows_components", "Windows update cleanup",
                 "Remove superseded Windows components using DISM. Administrator permission required.", (),
                 group="System maintenance", native_action="windows_components"),
        Category("delivery_optimization", "Delivery Optimization cache",
                 "Clear cached update downloads through Windows. Pinned items are kept. Administrator permission required.", (),
                 group="System maintenance", native_action="delivery_optimization"),
        Category("run_history", "Run dialog history", "Clear commands previously entered in the Run dialog.", (),
                 group="History", native_action="run_history"),
        Category("explorer_search_history", "File Explorer search history", "Clear previous File Explorer search terms.", (),
                 group="History", native_action="explorer_search_history"),
        Category("recent_document_history", "Recent document history", "Clear Windows recent-document records. Original documents are kept.", (),
                 group="History", native_action="recent_document_history"),
    ]
