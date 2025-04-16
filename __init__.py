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


def _get_model_upload_data(model_object, context):
    """Replicates parts of blenderkit.upload.get_upload_data for a specific model object."""
    if not model_object or not hasattr(model_object, "blenderkit"):
        print("BlenderKit Batch: Invalid model object passed to _get_model_upload_data")
        return None, None

    props = model_object.blenderkit
    asset_type = "MODEL"  # Assuming model, could potentially check props.asset_type
    export_data = {}
    upload_data = {
        "assetType": asset_type.lower(),
        "asset_base_id": props.asset_base_id,  # Needed for re-uploads
        "id": props.id,  # Needed for re-uploads
    }
    upload_params = {}

    # --- Basic Info from Props ---
    upload_data["displayName"] = props.name
    upload_data["description"] = props.description
    upload_data["tags"] = bk_utils.string2list(props.tags)

    # Category logic (simplified from original)
    if props.category == "" or props.category == "NONE":
        upload_data["category"] = asset_type.lower()
    else:
        upload_data["category"] = props.category
    if props.subcategory not in ("NONE", "EMPTY", "OTHER"):
        upload_data["category"] = props.subcategory
    if props.subcategory1 not in ("NONE", "EMPTY", "OTHER"):
        upload_data["category"] = props.subcategory1

    upload_data["isPrivate"] = props.is_private == "PRIVATE"
    # Read plan from batch settings, not individual props
    batch_props = context.scene.blenderkit_batch_props
    upload_data["freeFull"] = batch_props.upload_plan
    upload_data["license"] = (
        props.license if props.is_private == "PUBLIC" else "PRIVATE"
    )

    # Content Flags
    upload_data["sexualizedContent"] = props.sexualized_content
    upload_data["nudityContent"] = props.nudity_content
    upload_data["violenceContent"] = props.violence_content
    upload_data["sensitiveContentComment"] = props.sensitive_content_comment

    # --- Export Data --- (Paths and object names)
    export_data["asset_name"] = model_object.name  # Used by client_lib?
    export_data["thumbnail_path"] = ""
    if props.thumbnail:
        try:
            export_data["thumbnail_path"] = bpy.path.abspath(props.thumbnail)
        except Exception as e:
            print(
                f"BlenderKit Batch: Error resolving thumbnail path for {model_object.name}: {e}"
            )
            # Decide how to handle - fail, or continue without thumb path?

    # Hierarchy
    try:
        obs = bk_utils.get_hierarchy(model_object)
        obnames = [ob.name for ob in obs]
        export_data["models"] = obnames
    except Exception as e:
        print(f"BlenderKit Batch: Error getting hierarchy for {model_object.name}: {e}")
        return None, None  # Cannot proceed without hierarchy

    # --- Upload Params --- (Counts, modifiers, etc.) - Simplified
    # Calculating these accurately might require more context or checks.
    # Let's try sending what's stored on props, assuming BK updated them.
    upload_params["faceCount"] = props.face_count
    upload_params["vertexCount"] = props.vertex_count
    upload_params["materialCount"] = props.material_count
    upload_params["objectCount"] = len(obnames)
    upload_params["imageCount"] = props.image_count
    upload_params["imageMemory"] = props.image_memory
    upload_params["modifiers"] = bk_utils.string2list(props.modifiers)
    upload_params["dimensions"] = [
        props.dimension_x,
        props.dimension_y,
        props.dimension_z,
    ]

    # Add Blender version (important!)
    bk_utils.add_version(upload_data)

    # Merge upload_params into upload_data
    upload_data["upload_params"] = upload_params

    # --- Eval Paths (For progress reporting back to UI - maybe less relevant for batch?) ---
    export_data["eval_path_computing"] = (
        f"bpy.data.objects['{model_object.name}'].blenderkit.uploading"
    )
    export_data["eval_path_state"] = (
        f"bpy.data.objects['{model_object.name}'].blenderkit.upload_state"
    )
    export_data["eval_path"] = f"bpy.data.objects['{model_object.name}']"

    return export_data, upload_data


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
    """Gets called by the task queue to upload a specific model using client_lib."""
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
        f"BlenderKit Batch: Preparing non-interactive upload for {model.name} (Reupload: {is_reupload})"
    )
    props = model.blenderkit
    props.uploading = True
    props.upload_state = "0% - Preparing data (batch)"

    try:
        # --- Determine Upload Set ---+
        upload_set = []
        if not is_reupload:
            upload_set = ["METADATA", "THUMBNAIL", "MAINFILE"]
        else:
            if batch_props.reupload_metadata:
                upload_set.append("METADATA")
            if batch_props.reupload_thumbnail:
                upload_set.append("THUMBNAIL")
            if batch_props.reupload_main_file:
                upload_set.append("MAINFILE")

        if not upload_set:
            props.upload_state = "Skipped: No re-upload options selected."
            props.uploading = False
            print(
                f"BlenderKit Batch: Skipping re-upload for {model_name}, no components selected."
            )
            return

        # --- Prepare Data (Replicating parts of prepare_asset_data) ---+

        # Check thumbnail if needed
        if "THUMBNAIL" in upload_set and (
            not props.thumbnail
            or not bpy.path.abspath(props.thumbnail)
            or not os.path.exists(bpy.path.abspath(props.thumbnail))
        ):
            props.upload_state = (
                "Error: Thumbnail selected for upload but not found or path invalid."
            )
            props.uploading = False
            print(
                f"BlenderKit Batch: Upload error for {model_name} - Thumbnail missing."
            )
            return

        # Get export and upload data dictionaries
        export_data, upload_data = _get_model_upload_data(model, context)
        if not export_data or not upload_data:
            props.upload_state = "Error: Failed to gather upload data."
            props.uploading = False
            print(
                f"BlenderKit Batch: Upload error for {model_name} - Data preparation failed."
            )
            return

        # Set assetId for client_lib (expects 'assetId', not 'asset_base_id' or 'id')
        # Use asset_base_id if reuploading, otherwise it should be empty/None for new.
        upload_data["assetId"] = props.asset_base_id if is_reupload else None

        # Save temporary file if uploading main file (like prepare_asset_data does)
        temp_dir = None
        if "MAINFILE" in upload_set:
            # Check if file needs saving first? Main BK operator does.
            if not bpy.data.filepath:
                props.upload_state = "Error: Please save the main blend file first."
                props.uploading = False
                print(
                    f"BlenderKit Batch: Upload error for {model_name} - Main file not saved."
                )
                return
            try:
                temp_dir = tempfile.mkdtemp()
                _, ext = os.path.splitext(bpy.data.filepath)
                if not ext:
                    ext = ".blend"
                source_filepath = os.path.join(
                    temp_dir, f"export_{bk_paths.slugify(model.name)}{ext}"
                )
                # Save a copy of the *current* state
                bpy.ops.wm.save_as_mainfile(
                    filepath=source_filepath, compress=False, copy=True
                )
                export_data["source_filepath"] = source_filepath
                export_data["temp_dir"] = temp_dir
            except Exception as e:
                props.upload_state = f"Error saving temp file: {e}"
                props.uploading = False
                print(
                    f"BlenderKit Batch: Upload error for {model_name} - Failed to save temp file: {e}"
                )
                if temp_dir and os.path.exists(temp_dir):
                    import shutil

                    shutil.rmtree(temp_dir)
                return

        # --- Call Client Lib ---+
        print(f"BlenderKit Batch: Calling client_lib.asset_upload for {model.name}")
        props.upload_state = "1% - Sending to background uploader (batch)"
        bk_client_lib.asset_upload(upload_data, export_data, upload_set)
        # Note: Progress updates from here rely on the main BK timer checking client reports
        # and updating the props based on the eval_paths we provided in export_data.

    except Exception as e:
        props.upload_state = f"Error: {e}"
        props.uploading = False
        print(
            f"BlenderKit Batch: Unexpected error during upload prep for {model.name}: {e}"
        )
        import traceback

        traceback.print_exc()
        # Clean up temp dir if created
        if "temp_dir" in locals() and temp_dir and os.path.exists(temp_dir):
            import shutil

            shutil.rmtree(temp_dir)


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

    # --- Upload Plan ---+
    upload_plan: EnumProperty(
        name="Upload Plan",
        items=[
            ("FULL", "Full Plan", "Upload assets to the Full Plan (default)"),
            ("FREE", "Free", "Upload assets as Free for everyone"),
        ],
        description="Choose whether the batch-uploaded assets should be free or in the Full Plan",
        default="FULL",
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

        # --- Upload Settings ---+
        upload_settings_box = layout.box()
        upload_settings_box.label(text="Upload Settings:")
        upload_settings_box.prop(props, "upload_plan", expand=True)

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
