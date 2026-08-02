import { useEffect, useRef } from "react";
import * as THREE from "three";
import { useVrStage, VrStageNode } from "@/context/vr-stage-context";
import { useRendererMode } from "@/context/renderer-mode-context";
import { useVAD } from "@/context/vad-context";
import { useSubtitle } from "@/context/subtitle-context";
import { useAgentActivity } from "@/context/agent-activity-context";
import { useWebSocket } from "@/context/websocket-context";
import {
  floorToScene,
  headRelativeDelta,
  pressedEdge,
  rescaleThumbstick,
  snapTurnStep,
} from "@/utils/vr-helpers";

const SCALE = 0.01;
const MOVE_SPEED = 1.5;
const DEAD_ZONE = 0.2;
const SNAP_THRESHOLD = 0.7;
const SNAP_REARM = 0.3;
const SNAP_RADIANS = THREE.MathUtils.degToRad(30);
const CLICK_COOLDOWN_MS = 3000;

interface VrRuntime {
  scene: THREE.Scene;
  renderer: THREE.WebGLRenderer;
  camera: THREE.PerspectiveCamera;
  rig: THREE.Group;
  furniture: THREE.Group;
  avatar: THREE.Mesh | null;
  panel: THREE.Mesh | null;
  raycaster: THREE.Raycaster;
  controllers: THREE.XRTargetRaySpace[];
  hud: THREE.Mesh;
  hudTexture: THREE.Texture;
  turnArmed: boolean;
  xPressed: boolean;
  lastFrame: number;
  clickTimes: Map<string, number>;
}

interface PanelAction {
  collection: "plans" | "skills";
  id: string;
  decision: "approved" | "declined";
}

const providerRoutes: Record<string, string> = {
  todo_list: "/api/furniture/todo",
  timer_status: "/api/furniture/timers",
  google_gmail_unread: "/api/integrations/google/gmail/unread-status",
  bookshelf: "/api/furniture/bookshelf",
  reading_status: "/api/furniture/reading",
  plan_status: "/api/furniture/plans",
  skill_status: "/api/furniture/skills",
  drawing_board_image: "/api/furniture/drawing-board",
  google_calendar_status: "/api/integrations/google/status",
};

function documentToScene(stage: { room: { width: number; depth: number } }, x: number, y: number) {
  const position = floorToScene(stage.room, { x, y });
  return new THREE.Vector3(position.x, 0, position.z);
}

function canvasTexture(lines: string[], width = 768, height = 256) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d")!;
  context.fillStyle = "rgba(12, 18, 30, 0.92)";
  context.fillRect(0, 0, width, height);
  context.strokeStyle = "rgba(147, 197, 253, 0.9)";
  context.lineWidth = 5;
  context.strokeRect(3, 3, width - 6, height - 6);
  context.fillStyle = "#f8fafc";
  context.font = "600 34px system-ui, sans-serif";
  lines.slice(0, 5).forEach((line, index) => {
    const clipped = line.length > 48 ? `${line.slice(0, 47)}…` : line;
    context.fillText(clipped, 30, 52 + index * 43);
  });
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.needsUpdate = true;
  return texture;
}

function summaryLines(value: unknown): string[] {
  if (!value || typeof value !== "object") return [];
  const entries = Object.entries(value as Record<string, unknown>);
  return entries.slice(0, 4).map(([key, item]) => {
    if (typeof item === "string" || typeof item === "number" || typeof item === "boolean") return `${key}: ${String(item)}`;
    if (Array.isArray(item)) return `${key}: ${item.length} item(s)`;
    return `${key}: available`;
  });
}

export default function VrStage() {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const runtimeRef = useRef<VrRuntime | null>(null);
  const { stage, motionDurationMs } = useVrStage();
  const stageRef = useRef(stage);
  const { xrSession } = useRendererMode();
  const { micOn, startMic, stopMic } = useVAD();
  const micRef = useRef(micOn);
  const { subtitleText } = useSubtitle();
  const { activity } = useAgentActivity();
  const { sendMessage, baseUrl, wsState } = useWebSocket();
  const statusUntilRef = useRef(0);
  const initialMicOnRef = useRef(micOn);

  useEffect(() => { stageRef.current = stage; }, [stage]);
  useEffect(() => { micRef.current = micOn; }, [micOn]);

  useEffect(() => {
    if (xrSession && initialMicOnRef.current) void startMic();
  }, [xrSession, startMic]);

  useEffect(() => {
    if (!hostRef.current || !xrSession) return undefined;
    const host = hostRef.current;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#101827");
    const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.05, 50);
    const rig = new THREE.Group();
    rig.add(camera);
    scene.add(rig);
    scene.add(new THREE.AmbientLight(0xffffff, 1.4));
    const directional = new THREE.DirectionalLight(0xffffff, 1.2);
    directional.position.set(2, 5, 3);
    scene.add(directional);
    const furniture = new THREE.Group();
    scene.add(furniture);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.xr.enabled = true;
    renderer.xr.setReferenceSpaceType("local-floor");
    renderer.xr.setFoveation(1);
    host.appendChild(renderer.domElement);

    const controllers = [renderer.xr.getController(0), renderer.xr.getController(1)];
    controllers.forEach((controller) => {
      const points = [new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, -3)];
      controller.add(new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(points),
        new THREE.LineBasicMaterial({ color: 0x93c5fd }),
      ));
      scene.add(controller);
    });

    const hudTexture = canvasTexture(["Connecting to VR room…"]);
    const hud = new THREE.Mesh(
      new THREE.PlaneGeometry(1.2, 0.4),
      new THREE.MeshBasicMaterial({ map: hudTexture, transparent: true, depthTest: false }),
    );
    hud.position.set(0, -0.35, -1.3);
    hud.renderOrder = 100;
    camera.add(hud);

    const runtime: VrRuntime = {
      scene, renderer, camera, rig, furniture, avatar: null, panel: null,
      raycaster: new THREE.Raycaster(), controllers, hud, hudTexture,
      turnArmed: true, xPressed: false, lastFrame: performance.now(),
      clickTimes: new Map<string, number>(),
    };
    runtimeRef.current = runtime;

    const selectFurniture = async (node: VrStageNode) => {
      const now = performance.now();
      if (now - (runtime.clickTimes.get(node.id) || 0) >= CLICK_COOLDOWN_MS) {
        runtime.clickTimes.set(node.id, now);
        sendMessage({ type: "object-interaction", object_id: node.id });
      }
      const closingCurrent = runtime.panel?.userData.nodeId === node.id;
      if (runtime.panel) {
        scene.remove(runtime.panel);
        runtime.panel.traverse((object) => {
          if (object instanceof THREE.Mesh) {
            object.geometry.dispose();
            const materials = Array.isArray(object.material) ? object.material : [object.material];
            materials.forEach((material) => {
              if (material instanceof THREE.MeshBasicMaterial) material.map?.dispose();
              material.dispose();
            });
          }
        });
        runtime.panel = null;
      }
      if (closingCurrent || !node.uiWindow) return;
      const lines = [node.uiWindow.title, node.uiWindow.body || "", "Trigger the object again to close"];
      const route = node.uiWindow.provider ? providerRoutes[node.uiWindow.provider] : undefined;
      let liveData: Record<string, unknown> | null = null;
      if (route) {
        try {
          const response = await fetch(`${baseUrl}${route}`, { headers: { Accept: "application/json" } });
          if (response.ok) {
            liveData = await response.json() as Record<string, unknown>;
            lines.splice(2, 0, ...summaryLines(liveData));
            if (node.uiWindow.provider === "google_calendar_status" && liveData.connected !== true) {
              lines.splice(2, 0, "Exit VR to connect Google");
            }
          }
        } catch (_error) {
          lines.splice(2, 0, "Live data unavailable");
        }
      }
      const panelTexture = canvasTexture(lines, 768, 300);
      const panel = new THREE.Mesh(
        new THREE.PlaneGeometry(1.5, 0.59),
        new THREE.MeshBasicMaterial({ map: panelTexture, transparent: true, side: THREE.DoubleSide }),
      );
      const position = documentToScene(stageRef.current!, node.x, node.y);
      panel.position.set(position.x, Math.min(2.25, (node.elevation + node.height / 2 + 45) * SCALE), position.z);
      const headset = renderer.xr.getCamera();
      const headsetPosition = new THREE.Vector3();
      headset.getWorldPosition(headsetPosition);
      panel.lookAt(headsetPosition.x, panel.position.y, headsetPosition.z);
      panel.userData.panel = true;
      panel.userData.node = node;
      panel.userData.nodeId = node.id;
      const pending = Array.isArray(liveData?.pending) ? liveData.pending[0] as Record<string, unknown> | undefined : undefined;
      const collection = node.uiWindow.provider === "plan_status" ? "plans" : node.uiWindow.provider === "skill_status" ? "skills" : null;
      const decisionId = collection === "plans" ? pending?.plan_id : pending?.slug;
      if (collection && typeof decisionId === "string") {
        (["approved", "declined"] as const).forEach((decision, index) => {
          const button = new THREE.Mesh(
            new THREE.PlaneGeometry(0.55, 0.15),
            new THREE.MeshBasicMaterial({
              map: canvasTexture([decision === "approved" ? "Approve" : "Decline"], 320, 96),
              transparent: true,
            }),
          );
          button.position.set(index === 0 ? -0.32 : 0.32, -0.39, 0.01);
          button.userData.panelAction = { collection, id: decisionId, decision } satisfies PanelAction;
          panel.add(button);
        });
      }
      scene.add(panel);
      runtime.panel = panel;
    };

    const activatePanelAction = async (action: PanelAction) => {
      try {
        const response = await fetch(`${baseUrl}/api/furniture/${action.collection}/${encodeURIComponent(action.id)}/decision`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ decision: action.decision }),
        });
        statusUntilRef.current = performance.now() + 1500;
        if (!response.ok) throw new Error("Decision was rejected");
      } catch (_error) {
        statusUntilRef.current = performance.now() + 2500;
      }
    };

    const handleSelect = (event: THREE.Event & { target: THREE.Object3D }) => {
      const controller = event.target;
      const sourceIndex = controllers.indexOf(controller as THREE.XRTargetRaySpace);
      const source = xrSession.inputSources[sourceIndex];
      if (source?.handedness !== "right") return;
      const origin = new THREE.Vector3();
      const direction = new THREE.Vector3(0, 0, -1);
      controller.getWorldPosition(origin);
      direction.applyQuaternion(controller.getWorldQuaternion(new THREE.Quaternion()));
      runtime.raycaster.set(origin, direction);
      const hits = runtime.raycaster.intersectObjects([...(runtime.panel ? [runtime.panel] : []), furniture], true);
      const panelAction = hits.map((hit) => hit.object.userData.panelAction as PanelAction | undefined).find(Boolean);
      if (panelAction) {
        void activatePanelAction(panelAction);
        return;
      }
      const node = hits.map((hit) => hit.object.userData.node as VrStageNode | undefined).find(Boolean);
      if (node) void selectFurniture(node);
    };
    controllers.forEach((controller) => controller.addEventListener("select", handleSelect as never));

    const renderFrame = (time: number) => {
      const currentStage = stageRef.current;
      const dt = Math.min(0.05, (time - runtime.lastFrame) / 1000);
      runtime.lastFrame = time;
      const inputSources = [...xrSession.inputSources];
      inputSources.forEach((source, index) => {
        controllers[index].userData.handedness = source.handedness;
      });
      const left = inputSources.find((source) => source.handedness === "left")?.gamepad;
      const right = inputSources.find((source) => source.handedness === "right")?.gamepad;
      if (left && left.axes.length >= 4 && currentStage) {
        const axes = rescaleThumbstick(left.axes[2] || 0, left.axes[3] || 0, DEAD_ZONE);
        const xrCamera = renderer.xr.getCamera();
        const forward = xrCamera.getWorldDirection(new THREE.Vector3());
        forward.y = 0;
        if (forward.lengthSq() > 0) forward.normalize();
        const delta = headRelativeDelta(axes, forward, MOVE_SPEED, dt);
        rig.position.x += delta.x;
        rig.position.z += delta.z;
        const halfWidth = currentStage.room.width * SCALE / 2 - 0.25;
        const halfDepth = currentStage.room.depth * SCALE / 2 - 0.25;
        rig.position.x = THREE.MathUtils.clamp(rig.position.x, -halfWidth, halfWidth);
        rig.position.z = THREE.MathUtils.clamp(rig.position.z, -halfDepth, halfDepth);
      }
      if (right && right.axes.length >= 3) {
        const axis = right.axes[2] || 0;
        const turn = snapTurnStep(axis, runtime.turnArmed, SNAP_THRESHOLD, SNAP_REARM, SNAP_RADIANS);
        if (turn.delta) {
          const xrCamera = renderer.xr.getCamera();
          const before = xrCamera.getWorldPosition(new THREE.Vector3());
          rig.rotation.y += turn.delta;
          rig.updateMatrixWorld(true);
          const after = xrCamera.getWorldPosition(new THREE.Vector3());
          rig.position.x += before.x - after.x;
          rig.position.z += before.z - after.z;
        }
        runtime.turnArmed = turn.armed;
      }
      if (left) {
        if (left.buttons.length <= 4) {
          statusUntilRef.current = time + 1000;
        } else {
          const pressed = left.buttons[4].pressed;
          if (pressedEdge(pressed, runtime.xPressed)) {
            if (micRef.current) stopMic(); else void startMic();
            statusUntilRef.current = time + 1500;
          }
          runtime.xPressed = pressed;
        }
      }
      if (runtime.avatar?.userData.motion) {
        const motion = runtime.avatar.userData.motion as { from: THREE.Vector3; to: THREE.Vector3; start: number; duration: number };
        const progress = Math.min(1, (time - motion.start) / motion.duration);
        runtime.avatar.position.lerpVectors(motion.from, motion.to, progress);
        if (progress >= 1) delete runtime.avatar.userData.motion;
      }
      if (runtime.avatar) {
        const sprite = runtime.avatar.userData.sprite as {
          config: NonNullable<VrStageNode["spritesheet"]>;
          columns: number;
          rows: number;
          direction: "down" | "left" | "right" | "up";
        } | undefined;
        if (sprite) {
          const moving = Boolean(runtime.avatar.userData.motion);
          const pose = runtime.avatar.userData.pose;
          const animationName = pose === "sitting" ? "sit_down" : `${moving ? "walk" : "idle"}_${sprite.direction}`;
          const fallback = moving ? sprite.config.defaultAnimations?.walk : sprite.config.defaultAnimations?.idle;
          const animation = sprite.config.animations[animationName] || (fallback ? sprite.config.animations[fallback] : undefined);
          const material = runtime.avatar.material as THREE.MeshBasicMaterial;
          if (animation && material.map) {
            const fps = sprite.config.fps || 6;
            const frame = animation.frames[Math.floor(time / 1000 * fps) % animation.frames.length];
            material.map.repeat.set(1 / sprite.columns, 1 / sprite.rows);
            material.map.offset.set(frame / sprite.columns, 1 - (animation.row + 1) / sprite.rows);
          }
        }
        const headsetPosition = new THREE.Vector3();
        renderer.xr.getCamera().getWorldPosition(headsetPosition);
        runtime.avatar.lookAt(headsetPosition.x, runtime.avatar.position.y, headsetPosition.z);
      }
      renderer.render(scene, camera);
    };
    renderer.setAnimationLoop(renderFrame);
    void renderer.xr.setSession(xrSession);

    return () => {
      renderer.setAnimationLoop(null);
      controllers.forEach((controller) => controller.removeEventListener("select", handleSelect as never));
      renderer.dispose();
      host.removeChild(renderer.domElement);
      runtimeRef.current = null;
    };
  }, [xrSession, sendMessage, baseUrl, startMic, stopMic]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime || !stage) return;
    const { furniture, scene } = runtime;
    while (furniture.children.length) {
      const child = furniture.children.pop()!;
      child.traverse((object) => {
        if (object instanceof THREE.Mesh) {
          object.geometry.dispose();
          const materials = Array.isArray(object.material) ? object.material : [object.material];
            materials.forEach((material) => {
              if (material instanceof THREE.MeshBasicMaterial || material instanceof THREE.MeshLambertMaterial) {
                material.map?.dispose();
              }
              material.dispose();
            });
        }
      });
    }
    const loader = new THREE.TextureLoader();
    stage.nodes.filter((node) => node.type === "furniture").forEach((node) => {
      const solid = new THREE.MeshLambertMaterial({ color: node.color });
      const materials: THREE.Material[] = [solid, solid.clone(), solid.clone(), solid.clone(), solid.clone(), solid.clone()];
      if (node.texture?.src) {
        const texture = loader.load(node.texture.src);
        texture.colorSpace = THREE.SRGBColorSpace;
        texture.magFilter = THREE.NearestFilter;
        materials[5] = new THREE.MeshBasicMaterial({ map: texture, transparent: true });
      }
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(node.width * SCALE, node.height * SCALE, node.depth * SCALE),
        materials,
      );
      const position = documentToScene(stage, node.x, node.y);
      mesh.position.set(position.x, (node.elevation + node.height / 2) * SCALE, position.z);
      mesh.userData.node = node;
      mesh.traverse((child) => { child.userData.node = node; });
      furniture.add(mesh);
    });

    const roomWidth = stage.room.width * SCALE;
    const roomDepth = stage.room.depth * SCALE;
    const roomHeight = stage.room.height * SCALE;
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(roomWidth, roomDepth),
      new THREE.MeshLambertMaterial({ color: stage.room.floor_color, side: THREE.DoubleSide }),
    );
    floor.rotation.x = -Math.PI / 2;
    furniture.add(floor);
    const wallMaterial = new THREE.MeshLambertMaterial({ color: stage.room.wall_color, side: THREE.DoubleSide });
    const back = new THREE.Mesh(new THREE.PlaneGeometry(roomWidth, roomHeight), wallMaterial);
    back.position.set(0, roomHeight / 2, roomDepth / 2);
    back.rotation.y = Math.PI;
    furniture.add(back);
    const front = back.clone(); front.position.z = -roomDepth / 2; front.rotation.y = 0; furniture.add(front);
    const sideGeometry = new THREE.PlaneGeometry(roomDepth, roomHeight);
    const leftWall = new THREE.Mesh(sideGeometry, wallMaterial.clone());
    leftWall.position.set(-roomWidth / 2, roomHeight / 2, 0); leftWall.rotation.y = Math.PI / 2; furniture.add(leftWall);
    const rightWall = leftWall.clone(); rightWall.position.x = roomWidth / 2; rightWall.rotation.y = -Math.PI / 2; furniture.add(rightWall);

    const avatarNode = stage.nodes.find((node) => node.id === "avatar");
    if (avatarNode?.spritesheet) {
      if (!runtime.avatar) {
        const texture = loader.load(avatarNode.spritesheet.src);
        texture.colorSpace = THREE.SRGBColorSpace;
        texture.magFilter = THREE.NearestFilter;
        const columns = Math.max(...Object.values(avatarNode.spritesheet.animations).flatMap((animation) => animation.frames)) + 1;
        const rows = Math.max(...Object.values(avatarNode.spritesheet.animations).map((animation) => animation.row)) + 1;
        texture.repeat.set(1 / columns, 1 / rows);
        texture.offset.set(0, 1 - 1 / rows);
        runtime.avatar = new THREE.Mesh(
          new THREE.PlaneGeometry(avatarNode.width * SCALE, avatarNode.height * SCALE),
          new THREE.MeshBasicMaterial({ map: texture, transparent: true, side: THREE.DoubleSide }),
        );
        runtime.avatar.userData.sprite = { config: avatarNode.spritesheet, columns, rows, direction: "down" };
        scene.add(runtime.avatar);
      }
      const target = documentToScene(stage, avatarNode.x, avatarNode.y);
      target.y = avatarNode.height * SCALE / 2;
      if (runtime.avatar.position.lengthSq() === 0) runtime.avatar.position.copy(target);
      else if (!runtime.avatar.position.equals(target)) {
        const deltaX = target.x - runtime.avatar.position.x;
        const deltaZ = target.z - runtime.avatar.position.z;
        const sprite = runtime.avatar.userData.sprite as { direction: string } | undefined;
        if (sprite) sprite.direction = Math.abs(deltaX) > Math.abs(deltaZ) ? (deltaX > 0 ? "right" : "left") : (deltaZ < 0 ? "down" : "up");
        runtime.avatar.userData.motion = {
          from: runtime.avatar.position.clone(), to: target, start: performance.now(), duration: motionDurationMs,
        };
      }
      runtime.avatar.userData.pose = avatarNode.pose;
    }
    const spawn = documentToScene(stage, 480, 315);
    if (!runtime.rig.userData.spawned) {
      runtime.rig.position.set(spawn.x, 0, spawn.z);
      runtime.rig.rotation.y = Math.PI;
      runtime.rig.userData.spawned = true;
    }
  }, [stage, motionDurationMs]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    const status = performance.now() < statusUntilRef.current ? ` · Microphone ${micOn ? "on" : "off"}` : "";
    const activityText = activity.phase === "idle" ? "" : ` · ${activity.phase}`;
    const next = canvasTexture([
      subtitleText || "Embodied Agent",
      `${micOn ? "Mic on" : "Mic off"} · ${wsState}${activityText}${status}`,
    ]);
    (runtime.hud.material as THREE.MeshBasicMaterial).map = next;
    runtime.hudTexture.dispose();
    runtime.hudTexture = next;
  }, [subtitleText, activity, micOn, wsState]);

  return <div ref={hostRef} style={{ position: "fixed", inset: 0, background: "#101827" }} />;
}
