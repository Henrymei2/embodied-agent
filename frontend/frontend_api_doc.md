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
