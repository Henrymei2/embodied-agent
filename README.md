# Embodied Agent

## Minimal Working Product

Should implement following:
- basic 2d scene with three objects: bed, computer, calendar
  - calendar provides calendar tool
- frontend exposes get_object_position, move_to, list_objects, get_scene_status
- configure mcp server that includes frontend tools
- when sending requests to LLM, fetch scene status and only include calendar in tool list when agent is close to calendar


### Frontend

methods:
- get_object_position
- move_to


### Backend

Tool calls:




