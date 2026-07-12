get_object_position:
Parameter
Type
Required
Description
name
string
true
The string label of the object

returns
{
"type": "frontend_action_result",
"result": {
"ok": true,
"position": { "x": 420, "y": 180 }
}

stage document v2:
The frontend scene is stored as a stage document. Version 2 keeps the same
coordinate system as version 1, but nodes may now include texture and
spritesheet assets.

Asset src values may be http(s) URLs, data:image base64 URLs, or relative
public paths such as "./stage-assets/avatar.png". javascript: URLs are rejected.

Avatar spritesheet node example:
{
  "id": "avatar",
  "type": "avatar",
  "label": "avatar",
  "x": 480,
  "y": 270,
  "width": 70,
  "height": 92,
  "zIndex": 3,
  "text": "avatar",
  "spritesheet": {
    "src": "./stage-assets/avatar.png",
    "frameWidth": 48,
    "frameHeight": 64,
    "fps": 6,
    "defaultAnimations": {
      "idle": "idle_down",
      "walk": "walk_down"
    },
    "animations": {
      "idle_down": { "row": 0, "frames": [0, 1, 2, 1] },
      "idle_left": { "row": 1, "frames": [0, 1, 2, 1] },
      "idle_right": { "row": 2, "frames": [0, 1, 2, 1] },
      "idle_up": { "row": 3, "frames": [0, 1, 2, 1] },
      "walk_down": { "row": 4, "frames": [0, 1, 2, 3] },
      "walk_left": { "row": 5, "frames": [0, 1, 2, 3] },
      "walk_right": { "row": 6, "frames": [0, 1, 2, 3] },
      "walk_up": { "row": 7, "frames": [0, 1, 2, 3] }
    }
  }
}

Textured furniture node examples:
{
  "id": "computer",
  "type": "panel",
  "label": "computer",
  "x": 260,
  "y": 255,
  "width": 150,
  "height": 104,
  "zIndex": 2,
  "text": "computer",
  "texture": {
    "src": "./stage-assets/computer.png",
    "fit": "contain",
    "alt": "computer"
  }
}

{
  "id": "calendar",
  "type": "panel",
  "label": "calendar",
  "x": 480,
  "y": 420,
  "width": 118,
  "height": 92,
  "zIndex": 2,
  "text": "calendar",
  "texture": {
    "src": "./stage-assets/calendar.png",
    "fit": "contain",
    "alt": "calendar"
  }
}

{
  "id": "bed",
  "type": "panel",
  "label": "bed",
  "x": 700,
  "y": 270,
  "width": 188,
  "height": 118,
  "zIndex": 2,
  "text": "bed",
  "texture": {
    "src": "./stage-assets/bed.png",
    "fit": "contain",
    "alt": "bed"
  }
}

Replace a furniture texture:
{
  "type": "frontend_action_request",
  "request_id": "example-texture-patch",
  "action": "stage_patch_state",
  "args": {
    "patch": {
      "nodes": [
        {
          "id": "bed",
          "type": "panel",
          "label": "bed",
          "x": 700,
          "y": 270,
          "width": 188,
          "height": 118,
          "zIndex": 2,
          "text": "bed",
          "texture": {
            "src": "https://example.com/bed-texture.png",
            "fit": "contain",
            "alt": "bed"
          }
        }
      ]
    }
  }
}

move_to:
Parameter
Type
Required
Description
x
float
true
x-coordinate
y
float
true
y-coordinate

Asynchronous, returns when finished:
{
"type": "frontend_action_result",
"result": {
"ok": true
}

list_objects:
Parameter
Type
Required
Description
distance
float
false
Only returns objects within distance, default to positive infinity

returns
{
"type": "frontend_action_result",
"result": {
"ok": true,
"objects": {{“label”: “bed”, "x": 420, "y": 180} }
}

get_scene_status

returns
{
"type": "frontend_action_result",
"result": {
"ok": true,
"agent_position": {“x”: 100, “y”: 100},
“reachable_objects”: {“bed”, “calendar”}
}
