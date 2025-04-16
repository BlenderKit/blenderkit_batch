# This tiny lib is for importing any addon module from other addons,
# since with the introduction of extension repositories, the import
# path is not the same as in the past.

import bpy
import os


def find_module(module_name: str):
    """Find BlenderKit module in extension repositories and return its import path."""
    # print(f"DEBUG: Searching for module: {module_name}")

    try:
        import bl_ext

        # print(f"DEBUG: Successfully imported bl_ext")

        # Look through all modules in bl_ext
        bl_ext_modules = dir(bl_ext)
        # print(f"DEBUG: Found {len(bl_ext_modules)} modules in bl_ext: {bl_ext_modules}")

        for ext_module_name in bl_ext_modules:
            if ext_module_name.startswith("__"):  # Skip internal modules
                continue

            # print(f"DEBUG: Checking extension module: {ext_module_name}")
            try:
                # Get the module
                module = getattr(bl_ext, ext_module_name)
                # print(f"DEBUG: Got module object: {module}")

                # List all attributes in the module
                module_attrs = dir(module)
                # print(f"DEBUG: Module {ext_module_name} has attributes: {[attr for attr in module_attrs if not attr.startswith('__')]}")

                # Check if this module contains our target module
                if hasattr(module, module_name):
                    # print(f"DEBUG: Found {module_name} in bl_ext.{ext_module_name}")
                    return f"bl_ext.{ext_module_name}.{module_name}"
                # Also check if any submodule might contain it
                for attr in module_attrs:
                    if not attr.startswith("__"):
                        try:
                            submodule = getattr(module, attr)
                            if hasattr(submodule, module_name):
                                # print(f"DEBUG: Found {module_name} in bl_ext.{ext_module_name}.{attr}")
                                return f"bl_ext.{ext_module_name}.{attr}.{module_name}"
                        except Exception:
                            # some attributes might not be modules or accessible
                            pass
            except Exception as e:
                pass  # Silently ignore errors checking modules, they might not be fully loaded
            # print(f"DEBUG: Error checking module {ext_module_name}: {e}")
    except ImportError:
        # print(f"DEBUG: bl_ext not found, checking addon paths")
        pass  # bl_ext not found, continue with addon paths

    # Also check default addon paths as fallback
    addon_paths = bpy.utils.script_paths(subdir="addons")
    # print(f"DEBUG: Addon paths: {addon_paths}")

    for path in addon_paths:
        module_path = os.path.join(path, module_name)
        # print(f"DEBUG: Checking path: {module_path}")
        if os.path.exists(module_path):
            # print(f"DEBUG: Found {module_name} at {module_path}")
            return module_name

    # print(f"DEBUG: Module {module_name} not found anywhere")
    return None
