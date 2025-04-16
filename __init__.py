# This add-on is used to batch upload models to BlenderKit.
# It has several operators:
# - Render thumbnails of the selected models. Models are always a hierarchy, where top level parent (with no parents) is the model, that holds the .blenderkit data and the path to the thumbnail.
# - Upload all selected models to BlenderKit. If these were already uploaded, reupload them, with a popup similar to BlenderKit upload operator, where you can pick if to reupload main file, thumbnail, and metadata.
# Both operators work by using the BlenderKit add-on functions, and also Blenderkit client. All renders, and uploads are triggered with a delay, so that there aren't too many tasks run at once.
# The add-on has an UI panel. There are buttons for both of the operators.
# here is also a Enum, where the user can pick, if selected models, or all models in a collection are used. In case of a collection, there is a colleciton pick UI element.


bl_info = {
    "name": "BlenderKit Batch Operations",
    "author": "Your Name",  # TODO: Change author name
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "3D Viewport > Sidebar > BlenderKit Batch",
    "description": "Batch render thumbnails and upload models using BlenderKit.",
    "warning": "Requires the main BlenderKit addon to be enabled.",
    "doc_url": "",
    "category": "BlenderKit",
}

# --- Properties ---

import bpy
import importlib
import os
import tempfile
import json
from bpy.props import (
    EnumProperty,
    PointerProperty,
    FloatProperty,
    StringProperty,
    BoolProperty,
)
from bpy.types import Operator, Panel, PropertyGroup

# Try importing blenderkit utilities, handle error if blenderkit is not installed/enabled
try:
    # --- Dynamically Import BlenderKit ---+
    # Use bmodule_finder to locate the blenderkit installation (extension or addon)+
    from . import bmodule_finder

    bk_module_name = bmodule_finder.find_module("blenderkit")
    if not bk_module_name:
        raise ImportError("BlenderKit addon/extension not found.")

    # Import necessary submodules using the found path+
    bk_utils = importlib.import_module(f"{bk_module_name}.utils")
    bk_autothumb = importlib.import_module(f"{bk_module_name}.autothumb")
    bk_upload = importlib.import_module(f"{bk_module_name}.upload")
    bk_tasks_queue = importlib.import_module(f"{bk_module_name}.tasks_queue")
    bk_paths = importlib.import_module(f"{bk_module_name}.paths")
    bk_client_lib = importlib.import_module(f"{bk_module_name}.client_lib")
    BLENDERKIT_AVAILABLE = True
except ImportError:
    bk_utils = None
    bk_autothumb = None
    bk_upload = None
    bk_tasks_queue = None
    BLENDERKIT_AVAILABLE = False
    print("BlenderKit addon not found or enabled. Batch functionality will be limited.")

# --- Helper Functions ---


def _trigger_thumbnail_render(model_name):
    """Gets called by the task queue to render a thumbnail for a specific model."""
    if not BLENDERKIT_AVAILABLE:
        return

    model = bpy.data.objects.get(model_name)
    if not model:
        print(f"BlenderKit Batch: Model '{model_name}' not found for thumbnail render.")
        return

    # Ensure the file is saved, required for thumbnail path generation if relative
    if not bpy.data.filepath:
        print(
            f"BlenderKit Batch: Please save the blend file before rendering thumbnails."
        )
        # Ideally report back to the user interface
        # We could try generating in temp dir, but BK expects relative path '//'
        return  # Or handle error differently

    bkit = model.blenderkit
    print(f"BlenderKit Batch: Starting thumbnail generation for {model.name}")
    bkit.is_generating_thumbnail = True
    bkit.thumbnail_generating_state = "starting blender instance (batch)"

    try:
        tempdir = tempfile.mkdtemp()
        ext = ".blend"
        # Save a copy specifically for thumbnailing this asset
        temp_blend_filepath = os.path.join(
            tempdir, f"thumb_{bk_paths.slugify(model.name)}{ext}"
        )

        # Determine thumbnail path (similar logic to BK autothumb operator)
        thumb_dir = os.path.dirname(bpy.data.filepath)
        an_slug = bk_paths.slugify(model.name)
        thumb_path_base = os.path.join(thumb_dir, an_slug)
        rel_thumb_path_base = f"//{an_slug}"  # Use relative path

        # Find unique filename
        i = 0
        thumb_path = thumb_path_base + ".jpg"
        rel_thumb_path = rel_thumb_path_base + ".jpg"
        while os.path.exists(thumb_path):
            thumb_name = f"{an_slug}_{str(i).zfill(4)}"
            thumb_path = os.path.join(thumb_dir, thumb_name + ".jpg")
            rel_thumb_path = f"//{thumb_name}.jpg"
            i += 1

        # Set thumbnail path on the object's BK properties *before* saving copy
        bkit.thumbnail = rel_thumb_path
        bkit.thumbnail_generating_state = "Saving temp .blend file (batch)"

        # Save a copy including only the hierarchy of the current model?
        # BK original saves the *whole* scene copy. Let's stick to that for compatibility.
        bpy.ops.wm.save_as_mainfile(
            filepath=temp_blend_filepath, compress=False, copy=True
        )

        # Get hierarchy object names
        obs = bk_utils.get_hierarchy(model)
        obnames = [ob.name for ob in obs]

        # Prepare arguments dict
        args_dict = {
            "type": "model",  # Assuming 'model', could check obj.blenderkit.asset_type?
            "asset_name": model.name,
            "filepath": temp_blend_filepath,  # Pass the temp file to the background process
            "thumbnail_path": thumb_path,  # Absolute path for the render output
            "tempdir": tempdir,
        }
        thumbnail_args = {
            "models": str(obnames),
            "thumbnail_angle": bkit.thumbnail_angle,
            "thumbnail_snap_to": bkit.thumbnail_snap_to,
            "thumbnail_background_lightness": bkit.thumbnail_background_lightness,
            "thumbnail_resolution": bkit.thumbnail_resolution,
            "thumbnail_samples": bkit.thumbnail_samples,
            "thumbnail_denoising": bkit.thumbnail_denoising,
        }
        args_dict.update(thumbnail_args)

        # Start the background thumbnailer process
        bk_autothumb.start_model_thumbnailer(
            json_args=args_dict, props=bkit, wait=False, add_bg_process=True
        )
        print(f"BlenderKit Batch: Queued thumbnail generation for {model.name}")

    except Exception as e:
        bkit.is_generating_thumbnail = False
        bkit.thumbnail_generating_state = f"Error: {e}"
        print(f"BlenderKit Batch: Error preparing thumbnail for {model.name}: {e}")
        import traceback

        traceback.print_exc()


def _trigger_upload(model_name, is_reupload):
    """Gets called by the task queue to upload a specific model by invoking the main BlenderKit upload operator."""
    if not BLENDERKIT_AVAILABLE:
        return

    context = bpy.context
    batch_props = context.scene.blenderkit_batch_props
    model = bpy.data.objects.get(model_name)
    if not model or not hasattr(model, "blenderkit"):
        print(
            f"BlenderKit Batch: Model '{model_name}' not found or lacks .blenderkit data for upload."
        )
        return

    print(
        f"BlenderKit Batch: Triggering EXECUTE upload for {model.name} (Reupload: {is_reupload})"
    )
    props = model.blenderkit  # Get props for status updates later

    # --- Store current context ---+
    original_mode = context.mode
    original_active = context.view_layer.objects.active
    original_selected_names = {o.name for o in context.selected_objects}

    try:
        # --- Set context for the operator ---+
        # Ensure Object Mode
        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError as e:
                print(
                    f"BlenderKit Batch: Could not switch to Object Mode for {model_name}: {e}"
                )
                # Optionally set error status on props if available
                if props:
                    props.upload_state = "Error: Cannot switch to Object Mode"
                    props.uploading = False
                return  # Cannot proceed

        # Set selection and active object
        bpy.ops.object.select_all(action="DESELECT")
        model.select_set(True)
        # This is the crucial part for context
        context.view_layer.objects.active = model

        # --- Prepare operator properties ---+
        op_props = {
            "asset_type": "MODEL",  # Assuming MODEL
            "reupload": is_reupload,
            # Set components based on batch settings if reuploading, otherwise default to True for new
            "metadata": batch_props.reupload_metadata if is_reupload else True,
            "thumbnail": batch_props.reupload_thumbnail if is_reupload else True,
            "main_file": batch_props.reupload_main_file if is_reupload else True,
        }

        print(
            f"  - Attempting EXEC_DEFAULT: Reupload={op_props['reupload']}, Meta={op_props['metadata']}, Thumb={op_props['thumbnail']}, File={op_props['main_file']}"
        )
        # Use EXEC_DEFAULT for both new and re-uploads to try and bypass popups
        bpy.ops.object.blenderkit_upload("EXEC_DEFAULT", **op_props)

    except Exception as e:
        print(
            f"BlenderKit Batch: Error executing upload operator for {model.name}: {e}"
        )
        import traceback

        traceback.print_exc()
        if props:
            props.upload_state = f"Batch Upload Error: {e}"
            props.uploading = False  # Try to reset status

    finally:
        # --- Attempt to restore original context ---+
        # This might not be perfectly reliable across timed tasks
        try:
            # Restore selection first
            bpy.ops.object.select_all(action="DESELECT")
            for name in original_selected_names:
                obj = bpy.data.objects.get(name)
                if obj:
                    obj.select_set(True)
            # Restore active object
            context.view_layer.objects.active = original_active
            # Restore mode
            if context.mode != original_mode:
                try:
                    # Need to check if the mode is valid in the current context
                    if (
                        original_mode == "EDIT"
                        and context.view_layer.objects.active
                        and context.view_layer.objects.active.type == "MESH"
                    ):
                        bpy.ops.object.mode_set(mode=original_mode)
                    elif (
                        original_mode != "EDIT"
                    ):  # Avoid entering edit mode if active obj doesn't support it
                        bpy.ops.object.mode_set(mode=original_mode)
                except RuntimeError as mode_e:
                    print(
                        f"BlenderKit Batch: Warning - Could not restore mode to {original_mode}: {mode_e}"
                    )
        except Exception as restore_e:
            print(
                f"BlenderKit Batch: Warning - Failed to fully restore context after upload attempt for {model_name}: {restore_e}"
            )


class BlenderKitBatchProperties(PropertyGroup):
    target_mode: EnumProperty(
        name="Target Mode",
        description="Choose which models to process",
        items=[
            (
                "SELECTED",
                "Selected Models",
                "Process currently selected top-level models",
            ),
            (
                "COLLECTION",
                "Collection",
                "Process all top-level models in a specific collection",
            ),
        ],
        default="SELECTED",
    )  # type: ignore

    target_collection: PointerProperty(
        name="Target Collection",
        description="Collection containing models to process",
        type=bpy.types.Collection,
    )  # type: ignore

    task_delay: FloatProperty(
        name="Task Delay (seconds)",
        description="Delay between starting each thumbnail render or upload task",
        default=2.0,
        min=0.1,
        soft_max=30.0,
    )  # type: ignore

    # --- Re-upload Options ---+
    reupload_metadata: BoolProperty(
        name="Metadata",
        description="Re-upload asset metadata (name, description, tags, etc.)",
        default=True,
    )  # type: ignore

    reupload_thumbnail: BoolProperty(
        name="Thumbnail",
        description="Re-upload the asset thumbnail image",
        default=True,
    )  # type: ignore

    reupload_main_file: BoolProperty(
        name="Main File",
        description="Re-upload the main asset .blend file or source file",
        default=True,
    )  # type: ignore


# --- Operators ---


class BK_BATCH_OT_render_thumbnails(Operator):
    """Batch render thumbnails for selected/collection models using BlenderKit"""

    bl_idname = "bk_batch.render_thumbnails"
    bl_label = "Batch Render Thumbnails"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        # Only allow if BlenderKit is available and we have either a selection or a collection set
        props = context.scene.blenderkit_batch_props
        # Also check if file is saved, as it's needed for relative thumbnail paths
        return (
            BLENDERKIT_AVAILABLE
            and bpy.data.filepath != ""
            and (
                props.target_mode == "SELECTED"
                and context.selected_objects
                or props.target_mode == "COLLECTION"
                and props.target_collection
            )
        )

    def execute(self, context):
        if not BLENDERKIT_AVAILABLE:
            self.report({"ERROR"}, "BlenderKit addon is not available.")
            return {"CANCELLED"}
        if not bpy.data.filepath:
            self.report({"ERROR"}, "Please save the blend file first.")
            return {"CANCELLED"}

        props = context.scene.blenderkit_batch_props
        models_to_process = []

        if props.target_mode == "SELECTED":
            # Use blenderkit utility to find top-level selected models with BK data
            models_to_process = bk_utils.get_selected_models()
            if not models_to_process:
                self.report(
                    {"WARNING"},
                    "No valid BlenderKit models selected (ensure top-level parent is selected).",
                )
                return {"CANCELLED"}
        elif props.target_mode == "COLLECTION":
            if not props.target_collection:
                self.report({"ERROR"}, "No target collection selected.")
                return {"CANCELLED"}
            # Find top-level objects in the collection
            for obj in props.target_collection.objects:
                # Check if it's a top-level parent and seems like a BK asset
                if (
                    obj.parent is None
                    and hasattr(obj, "blenderkit")
                    and (
                        obj.blenderkit.name != ""
                        or obj.blenderkit.asset_base_id != ""
                        or obj.instance_collection is not None
                    )
                ):
                    models_to_process.append(obj)
            if not models_to_process:
                self.report(
                    {"WARNING"},
                    f"No valid BlenderKit models found in collection '{props.target_collection.name}'.",
                )
                return {"CANCELLED"}

        if not models_to_process:
            self.report({"WARNING"}, "No models found to process.")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"Queuing thumbnail generation for {len(models_to_process)} models...",
        )

        # Iterate through models_to_process and add tasks to the queue
        for index, model in enumerate(models_to_process):
            delay = index * props.task_delay
            bk_tasks_queue.add_task(
                (_trigger_thumbnail_render, (model.name,)), wait=delay
            )
            print(
                f"Scheduled thumbnail render for '{model.name}' with delay {delay:.2f}s"
            )

        return {"FINISHED"}


class BK_BATCH_OT_upload_models(Operator):
    """Batch upload selected/collection models using BlenderKit"""

    bl_idname = "bk_batch.upload_models"
    bl_label = "Batch Upload Models"
    bl_options = {"REGISTER"}  # Removed UNDO as it interacts with other ops/context

    @classmethod
    def poll(cls, context):
        # Similar poll logic to the render operator
        props = context.scene.blenderkit_batch_props
        # Check BK login status by directly checking blenderkit preferences
        logged_in = False
        if BLENDERKIT_AVAILABLE:
            # Get the actual blenderkit addon preferences
            blenderkit_prefs_obj = context.preferences.addons.get(bk_module_name, None)
            if blenderkit_prefs_obj:
                logged_in = (
                    getattr(blenderkit_prefs_obj.preferences, "api_key", "") != ""
                )

        return (
            BLENDERKIT_AVAILABLE
            and logged_in
            and (
                props.target_mode == "SELECTED"
                and context.selected_objects
                or props.target_mode == "COLLECTION"
                and props.target_collection
            )
        )

    def execute(self, context):
        if not BLENDERKIT_AVAILABLE:
            self.report({"ERROR"}, "BlenderKit addon is not available.")
            return {"CANCELLED"}

        # Re-check login just in case something changed between poll and execute
        blenderkit_prefs_obj = context.preferences.addons.get(bk_module_name, None)
        if (
            not blenderkit_prefs_obj
            or getattr(blenderkit_prefs_obj.preferences, "api_key", "") == ""
        ):
            self.report({"ERROR"}, "Please log in to BlenderKit first.")
            return {"CANCELLED"}

        props = context.scene.blenderkit_batch_props
        models_to_process = []

        if props.target_mode == "SELECTED":
            models_to_process = bk_utils.get_selected_models()
            if not models_to_process:
                self.report(
                    {"WARNING"},
                    "No valid BlenderKit models selected (ensure top-level parent is selected).",
                )
                return {"CANCELLED"}
        elif props.target_mode == "COLLECTION":
            if not props.target_collection:
                self.report({"ERROR"}, "No target collection selected.")
                return {"CANCELLED"}
            for obj in props.target_collection.objects:
                if (
                    obj.parent is None
                    and hasattr(obj, "blenderkit")
                    and (
                        obj.blenderkit.name != ""
                        or obj.blenderkit.asset_base_id != ""
                        or obj.instance_collection is not None
                    )
                ):
                    models_to_process.append(obj)
            if not models_to_process:
                self.report(
                    {"WARNING"},
                    f"No valid BlenderKit models found in collection '{props.target_collection.name}'.",
                )
                return {"CANCELLED"}

        if not models_to_process:
            self.report({"WARNING"}, "No models found to process.")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Queuing upload for {len(models_to_process)} models...")

        # Iterate through models_to_process and add tasks to the queue
        for index, model in enumerate(models_to_process):
            # Determine if it's a re-upload by checking asset_base_id
            is_reupload = model.blenderkit.asset_base_id != ""
            delay = index * props.task_delay
            bk_tasks_queue.add_task(
                (_trigger_upload, (model.name, is_reupload)), wait=delay
            )
            print(
                f"Scheduled upload for '{model.name}' (Reupload: {is_reupload}) with delay {delay:.2f}s"
            )

        return {"FINISHED"}


# --- Panel ---


class BK_BATCH_PT_panel(Panel):
    """Creates a Panel in the Object properties window"""

    bl_label = "BlenderKit Batch"
    bl_idname = "BK_BATCH_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlenderKit Batch"  # New tab in the sidebar

    def draw_header(self, context):
        layout = self.layout
        # Show different icon based on login status
        icon = "MOD_DATA_TRANSFER"
        logged_in = False
        if BLENDERKIT_AVAILABLE:
            blenderkit_prefs_obj = context.preferences.addons.get(bk_module_name, None)
            if blenderkit_prefs_obj:
                logged_in = (
                    getattr(blenderkit_prefs_obj.preferences, "api_key", "") != ""
                )

        if not BLENDERKIT_AVAILABLE:
            icon = "ERROR"
        elif not logged_in:
            icon = "USER"
        layout.label(text="", icon=icon)

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        props = scene.blenderkit_batch_props

        if not BLENDERKIT_AVAILABLE:
            box = layout.box()
            box.label(text="BlenderKit Addon Not Found!", icon="ERROR")
            box.label(text="Please install or enable the BlenderKit addon.")
            return

        # Check login status directly from blenderkit preferences
        logged_in = False
        blenderkit_prefs_obj = context.preferences.addons.get(bk_module_name, None)
        if blenderkit_prefs_obj:
            logged_in = getattr(blenderkit_prefs_obj.preferences, "api_key", "") != ""

        # --- Target Settings ---+
        box = layout.box()
        box.label(text="Target Models:")
        row = box.row(align=True)
        row.prop(props, "target_mode", expand=True)
        if props.target_mode == "COLLECTION":
            row.prop(props, "target_collection", text="")

        # --- Task Settings ---+
        box.prop(props, "task_delay")

        # --- Re-upload Options ---+
        reupload_box = layout.box()
        reupload_box.label(text="Re-upload Options (when applicable):")
        reupload_col = reupload_box.column(align=True)
        reupload_col.prop(props, "reupload_metadata")
        reupload_col.prop(props, "reupload_thumbnail")
        reupload_col.prop(props, "reupload_main_file")

        # --- Actions ---+
        action_box = layout.box()
        col = action_box.column(align=True)
        col.label(text="Actions:")

        # Render Thumbnails Button
        render_row = col.row(align=True)
        render_row.operator(
            BK_BATCH_OT_render_thumbnails.bl_idname, icon="RENDER_STILL"
        )
        if bpy.data.filepath == "":
            render_row.enabled = False
            render_row.label(text="Save file first", icon="ERROR")

        # Upload Models Button
        upload_row = col.row(align=True)
        upload_op = upload_row.operator(
            BK_BATCH_OT_upload_models.bl_idname, icon="EXPORT"
        )
        if not logged_in:
            upload_row.enabled = False
            # Add button to open preferences or show login?
            upload_row.label(text="Login required", icon="USER")


# --- Register ---

classes = (
    BlenderKitBatchProperties,
    BK_BATCH_OT_render_thumbnails,
    BK_BATCH_OT_upload_models,
    BK_BATCH_PT_panel,
)


def register():
    if not BLENDERKIT_AVAILABLE:
        print("Cannot register BlenderKit Batch: Main BlenderKit addon not found.")
        # Optionally register a dummy panel showing the error?
        return

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.blenderkit_batch_props = PointerProperty(
        type=BlenderKitBatchProperties
    )


def unregister():
    # Check if registered before unregistering
    if hasattr(bpy.types.Scene, "blenderkit_batch_props"):
        # Don't unregister if BlenderKit wasn't available during registration
        if not BLENDERKIT_AVAILABLE and not hasattr(
            bpy.types, BK_BATCH_PT_panel.bl_idname
        ):
            print(
                "Skipping BlenderKit Batch unregistration as it wasn't fully registered."
            )
            return

        del bpy.types.Scene.blenderkit_batch_props

        for cls in reversed(classes):
            # Handle cases where a class might not have been registered due to BK check
            if hasattr(bpy.types, cls.bl_idname):
                bpy.utils.unregister_class(cls)
            elif hasattr(bpy, cls.__name__):  # PropertyGroups might not have bl_idname
                if cls in classes:  # Ensure it's one of ours
                    try:
                        bpy.utils.unregister_class(cls)
                    except RuntimeError:
                        print(f"Could not unregister {cls.__name__}")


if __name__ == "__main__":
    # Allow running the script directly in Blender Text Editor for testing
    # Note: This might not fully work if BlenderKit wasn't loaded normally
    try:
        unregister()  # Ensure clean state if run multiple times
    except Exception as e:
        print(f"Unregister failed: {e}")
        pass
    register()
