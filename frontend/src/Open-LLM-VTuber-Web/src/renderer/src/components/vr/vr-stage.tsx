import { useEffect, useRef, useState } from "react";
import { Button } from "@chakra-ui/react";
import { FiX } from "react-icons/fi";
import * as THREE from "three";
import { useVrStage, VrStageNode } from "@/context/vr-stage-context";
import { useRendererMode } from "@/context/renderer-mode-context";
import { useVAD } from "@/context/vad-context";
import { useSubtitle } from "@/context/subtitle-context";
import { useAgentActivity } from "@/context/agent-activity-context";
import { useWebSocket } from "@/context/websocket-context";
import { audioManager } from "@/utils/audio-manager";
import {
  floorToScene,
  furnitureCenterSceneY,
  furnitureDecalLayouts,
  headRelativeDelta,
  inputSourcesByHand,
  isViewerPositionClear,
  pressedEdge,
  questLocomotionStick,
  rescaleThumbstick,
  selectViewerSpawn,
  snapTurnStep,
  clampCaptionText,
  viewerForwardFromPose,
  viewerPoseCamera,
} from "@/utils/vr-helpers";

const SCALE = 0.01;
const MOVE_SPEED = 1.5;
const DEAD_ZONE = 0.2;
const SNAP_THRESHOLD = 0.7;
const SNAP_REARM = 0.3;
const SNAP_RADIANS = THREE.MathUtils.degToRad(30);

interface VrRuntime {
  scene: THREE.Scene;
  renderer: THREE.WebGLRenderer;
  camera: THREE.PerspectiveCamera;
  rig: THREE.Group;
  furniture: THREE.Group;
  avatar: THREE.Group | null;
  panel: THREE.Mesh | null;
  raycaster: THREE.Raycaster;
  controllers: THREE.XRTargetRaySpace[];
  hud: THREE.Mesh;
  hudTexture: THREE.Texture;
  turnArmed: boolean;
  xPressed: boolean;
  lastFrame: number;
  collisionNodes: VrStageNode[];
  panelGeneration: number;
  textureWarnings: Set<string>;
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

function disposeObject(root: THREE.Object3D) {
  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh || object instanceof THREE.Line)) return;
    object.geometry.dispose();
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    materials.forEach((material) => {
      if (
        material instanceof THREE.MeshBasicMaterial ||
        material instanceof THREE.MeshLambertMaterial ||
        material instanceof THREE.MeshStandardMaterial
      ) material.map?.dispose();
      material.dispose();
    });
  });
}

function roundedRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  context.beginPath();
  context.roundRect(x, y, width, height, radius);
  context.closePath();
}

function canvasTexture(lines: string[], width = 768, height = 256) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d")!;
  const background = context.createLinearGradient(0, 0, width, height);
  background.addColorStop(0, "rgba(15, 23, 42, 0.96)");
  background.addColorStop(1, "rgba(7, 12, 24, 0.96)");
  roundedRect(context, 4, 4, width - 8, height - 8, 28);
  context.fillStyle = background;
  context.fill();
  context.strokeStyle = "rgba(199, 210, 254, 0.42)";
  context.lineWidth = 3;
  context.stroke();
  context.fillStyle = "#f8fafc";
  context.font = "600 34px system-ui, sans-serif";
  lines.slice(0, 5).forEach((line, index) => {
    const clipped = line.length > 48 ? `${line.slice(0, 47)}…` : line;
    context.fillText(clipped, 30, 52 + index * 43);
  });
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.needsUpdate = true;
  return texture;
}

function captionTexture(caption: string, status: string) {
  const width = 1536;
  const height = 384;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d")!;
  const background = context.createLinearGradient(0, 0, width, height);
  background.addColorStop(0, "rgba(12, 18, 32, 0.9)");
  background.addColorStop(1, "rgba(7, 10, 20, 0.84)");
  roundedRect(context, 8, 8, width - 16, height - 16, 64);
  context.fillStyle = background;
  context.fill();
  context.strokeStyle = "rgba(255, 255, 255, 0.17)";
  context.lineWidth = 4;
  context.stroke();

  context.fillStyle = "rgba(199, 210, 254, 0.92)";
  context.font = "650 30px system-ui, sans-serif";
  context.letterSpacing = "2px";
  context.fillText(status.toUpperCase(), 72, 68);
  context.letterSpacing = "0px";
  context.fillStyle = "#f8fafc";
  context.font = "600 54px system-ui, sans-serif";
  clampCaptionText(caption, 48, 3).forEach((line, index) => {
    context.fillText(line, 72, 146 + index * 70);
  });

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.needsUpdate = true;
  return texture;
}

function createSpatialAvatar(node: VrStageNode) {
  const avatar = new THREE.Group();
  avatar.name = "spatial-companion";
  const shell = new THREE.MeshStandardMaterial({
    color: new THREE.Color(node.color), roughness: 0.28, metalness: 0.16,
  });
  const porcelain = new THREE.MeshStandardMaterial({
    color: 0xf1f5ff, roughness: 0.22, metalness: 0.08,
  });
  const darkGlass = new THREE.MeshStandardMaterial({
    color: 0x0b1020, roughness: 0.12, metalness: 0.5,
  });
  const glow = new THREE.MeshStandardMaterial({
    color: 0xc7d2fe, emissive: 0x818cf8, emissiveIntensity: 2.1, roughness: 0.18,
  });

  const shadow = new THREE.Mesh(
    new THREE.CircleGeometry(0.42, 48),
    new THREE.MeshBasicMaterial({ color: 0x05070d, transparent: true, opacity: 0.23, depthWrite: false }),
  );
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = 0.006;
  avatar.add(shadow);

  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.25, 0.43, 8, 20), shell);
  body.position.y = 0.72;
  body.castShadow = true;
  avatar.add(body);
  const chest = new THREE.Mesh(new THREE.CircleGeometry(0.075, 32), glow);
  chest.position.set(0, 0.77, 0.255);
  avatar.add(chest);

  const head = new THREE.Mesh(new THREE.SphereGeometry(0.34, 40, 24), porcelain);
  head.scale.set(1.04, 0.9, 0.92);
  head.position.y = 1.25;
  head.castShadow = true;
  avatar.add(head);
  // A shallow spherical visor follows the head instead of floating as a flat
  // plane in front of it. The slightly larger radius prevents z-fighting while
  // preserving the same silhouette and material treatment.
  const face = new THREE.Mesh(
    new THREE.SphereGeometry(
      0.347,
      32,
      12,
      Math.PI / 2 - 0.64,
      1.28,
      Math.PI / 2 - 0.31,
      0.62,
    ),
    darkGlass,
  );
  face.scale.copy(head.scale);
  face.position.y = 1.25;
  avatar.add(face);
  const leftEye = new THREE.Mesh(new THREE.CircleGeometry(0.035, 24), glow);
  leftEye.position.set(-0.09, 1.27, 0.307);
  avatar.add(leftEye);
  const rightEye = leftEye.clone();
  rightEye.position.x = 0.09;
  avatar.add(rightEye);

  const armGeometry = new THREE.CapsuleGeometry(0.065, 0.33, 6, 14);
  const leftArm = new THREE.Mesh(armGeometry, porcelain);
  leftArm.position.set(-0.34, 0.72, 0);
  leftArm.rotation.z = -0.12;
  leftArm.castShadow = true;
  avatar.add(leftArm);
  const rightArm = leftArm.clone();
  rightArm.position.x = 0.34;
  rightArm.rotation.z = 0.12;
  avatar.add(rightArm);

  const legGeometry = new THREE.CapsuleGeometry(0.075, 0.25, 6, 14);
  const leftLeg = new THREE.Mesh(legGeometry, shell);
  leftLeg.position.set(-0.12, 0.26, 0);
  leftLeg.castShadow = true;
  avatar.add(leftLeg);
  const rightLeg = leftLeg.clone();
  rightLeg.position.x = 0.12;
  avatar.add(rightLeg);

  const halo = new THREE.Mesh(new THREE.TorusGeometry(0.43, 0.008, 8, 64), glow);
  halo.position.set(0, 1.25, -0.13);
  avatar.add(halo);
  const presenceLight = new THREE.PointLight(0x818cf8, 0.65, 2.4, 2);
  presenceLight.position.set(0, 1.05, 0.35);
  avatar.add(presenceLight);
  avatar.userData.visuals = { body, head, leftArm, rightArm, chest, halo };
  return avatar;
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
  const { xrSession, leaveVr } = useRendererMode();
  const { micOn, startMic, stopMic } = useVAD();
  const micRef = useRef(micOn);
  const { subtitleText } = useSubtitle();
  const { activity } = useAgentActivity();
  const { baseUrl, wsState } = useWebSocket();
  const baseUrlRef = useRef(baseUrl);
  const startMicRef = useRef(startMic);
  const stopMicRef = useRef(stopMic);
  const micStatusTimerRef = useRef<number | null>(null);
  const warningTimerRef = useRef<number | null>(null);
  const [micStatus, setMicStatus] = useState<string | null>(null);
  const [vrWarning, setVrWarning] = useState<string | null>(null);
  const [audioWarning, setAudioWarning] = useState<string | null>(null);
  const initialMicOnRef = useRef(micOn);

  useEffect(() => { stageRef.current = stage; }, [stage]);
  useEffect(() => { micRef.current = micOn; }, [micOn]);
  useEffect(() => { baseUrlRef.current = baseUrl; }, [baseUrl]);
  useEffect(() => { startMicRef.current = startMic; }, [startMic]);
  useEffect(() => { stopMicRef.current = stopMic; }, [stopMic]);

  useEffect(() => {
    if (xrSession && initialMicOnRef.current) void startMicRef.current();
  }, [xrSession]);

  useEffect(() => () => {
    if (micStatusTimerRef.current !== null) window.clearTimeout(micStatusTimerRef.current);
    if (warningTimerRef.current !== null) window.clearTimeout(warningTimerRef.current);
  }, []);

  useEffect(() => audioManager.subscribe((state) => {
    setAudioWarning(state.state === "error" ? state.message : null);
  }), []);

  useEffect(() => {
    if (!hostRef.current || !xrSession) return undefined;
    const host = hostRef.current;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#0c1120");
    scene.fog = new THREE.FogExp2("#111827", 0.025);
    const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.05, 50);
    const rig = new THREE.Group();
    rig.add(camera);
    scene.add(rig);
    scene.add(new THREE.HemisphereLight(0xeef2ff, 0x51422e, 1.55));
    const directional = new THREE.DirectionalLight(0xfff4df, 2.25);
    directional.position.set(-2.5, 5.5, 2.8);
    directional.castShadow = true;
    directional.shadow.mapSize.set(1024, 1024);
    directional.shadow.camera.near = 0.1;
    directional.shadow.camera.far = 16;
    directional.shadow.bias = -0.0008;
    scene.add(directional);
    const roomGlow = new THREE.PointLight(0x8ea4ff, 1.2, 8, 2);
    roomGlow.position.set(2.8, 2.3, -1.8);
    scene.add(roomGlow);
    const furniture = new THREE.Group();
    scene.add(furniture);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.08;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.xr.enabled = true;
    renderer.xr.setReferenceSpaceType("local-floor");
    host.appendChild(renderer.domElement);

    const controllers = [renderer.xr.getController(0), renderer.xr.getController(1)];
    controllers.forEach((controller) => {
      const points = [new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, -2.2)];
      const ray = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(points),
        new THREE.LineBasicMaterial({ color: 0xc7d2fe, transparent: true, opacity: 0.58 }),
      );
      ray.visible = false;
      controller.add(ray);
      const reticle = new THREE.Mesh(
        new THREE.RingGeometry(0.009, 0.014, 24),
        new THREE.MeshBasicMaterial({ color: 0xe0e7ff, transparent: true, opacity: 0.82, side: THREE.DoubleSide }),
      );
      reticle.position.z = -2.2;
      reticle.visible = false;
      controller.add(reticle);
      // Target-ray poses are reported in XR reference-space coordinates. Keep
      // them under the same locomotion rig as the headset so walking and snap
      // turns transform the head and hands together.
      rig.add(controller);
    });

    const hudTexture = captionTexture("Preparing your companion space", "Connecting");
    const hud = new THREE.Mesh(
      new THREE.PlaneGeometry(1.12, 0.28),
      new THREE.MeshBasicMaterial({ map: hudTexture, transparent: true, depthTest: false }),
    );
    hud.position.set(0, -0.48, -1.62);
    hud.renderOrder = 100;
    camera.add(hud);

    const runtime: VrRuntime = {
      scene, renderer, camera, rig, furniture, avatar: null, panel: null,
      raycaster: new THREE.Raycaster(), controllers, hud, hudTexture,
      turnArmed: true, xPressed: false, lastFrame: performance.now(),
      collisionNodes: [],
      panelGeneration: 0,
      textureWarnings: new Set<string>(),
    };
    runtimeRef.current = runtime;

    const setPanelLines = (panel: THREE.Mesh, lines: string[]) => {
      const material = panel.material as THREE.MeshBasicMaterial;
      const previous = material.map;
      material.map = canvasTexture(lines, 768, 300);
      material.needsUpdate = true;
      previous?.dispose();
    };

    const closePanel = () => {
      runtime.panelGeneration += 1;
      if (!runtime.panel) return;
      scene.remove(runtime.panel);
      disposeObject(runtime.panel);
      runtime.panel = null;
    };

    const selectFurniture = async (node: VrStageNode) => {
      const closingCurrent = runtime.panel?.userData.nodeId === node.id;
      closePanel();
      if (closingCurrent) return;
      const windowConfig: NonNullable<VrStageNode["uiWindow"]> = node.uiWindow || {
        title: node.label || node.id,
        body: "No additional information is available for this furniture.",
      };
      const generation = runtime.panelGeneration;
      const lines = [windowConfig.title, windowConfig.body || "", "Loading…"];
      const panelTexture = canvasTexture(lines, 768, 300);
      const panel = new THREE.Mesh(
        new THREE.PlaneGeometry(1.5, 0.59),
        new THREE.MeshBasicMaterial({ map: panelTexture, transparent: true, side: THREE.DoubleSide }),
      );
      const position = documentToScene(stageRef.current!, node.x, node.y);
      panel.position.set(position.x, Math.min(2.25, (node.elevation + node.height / 2 + 45) * SCALE), position.z);
      const headset = viewerPoseCamera(renderer.xr.getCamera());
      const headsetPosition = new THREE.Vector3();
      headset.getWorldPosition(headsetPosition);
      panel.lookAt(headsetPosition.x, panel.position.y, headsetPosition.z);
      panel.userData.panel = true;
      panel.userData.node = node;
      panel.userData.nodeId = node.id;
      const closeButton = new THREE.Mesh(
        new THREE.PlaneGeometry(0.22, 0.12),
        new THREE.MeshBasicMaterial({ map: canvasTexture(["Close"], 220, 96), transparent: true }),
      );
      closeButton.position.set(0.58, 0.22, 0.012);
      closeButton.userData.panelClose = true;
      panel.add(closeButton);
      scene.add(panel);
      runtime.panel = panel;

      const route = windowConfig.provider ? providerRoutes[windowConfig.provider] : undefined;
      let liveData: Record<string, unknown> | null = null;
      if (route) {
        try {
          const response = await fetch(`${baseUrlRef.current}${route}`, { headers: { Accept: "application/json" } });
          if (response.ok) {
            liveData = await response.json() as Record<string, unknown>;
            lines.splice(2, 0, ...summaryLines(liveData));
            if (windowConfig.provider === "google_calendar_status" && liveData.connected !== true) {
              lines.splice(2, 0, "Exit VR to connect Google");
            }
          }
        } catch (_error) {
          lines.splice(2, 0, "Live data unavailable");
        }
      }
      if (runtime.panel !== panel || runtime.panelGeneration !== generation) return;
      lines.splice(lines.indexOf("Loading…"), 1, "Select the furniture again to close");
      setPanelLines(panel, lines);
      const pending = Array.isArray(liveData?.pending) ? liveData.pending[0] as Record<string, unknown> | undefined : undefined;
      const collection = windowConfig.provider === "plan_status" ? "plans" : windowConfig.provider === "skill_status" ? "skills" : null;
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
    };

    const activatePanelAction = async (action: PanelAction) => {
      try {
        const response = await fetch(`${baseUrlRef.current}/api/furniture/${action.collection}/${encodeURIComponent(action.id)}/decision`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ decision: action.decision }),
        });
        if (!response.ok) throw new Error("Decision was rejected");
      } catch (_error) {
        // The furniture panel remains open so the user can retry.
      }
    };

    const handleSelect = (event: THREE.Event & { target: THREE.Object3D }) => {
      const controller = event.target;
      if (controller.userData.handedness !== "right") return;
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
      if (hits.some((hit) => hit.object.userData.panelClose === true)) {
        closePanel();
        return;
      }
      const node = hits.map((hit) => hit.object.userData.node as VrStageNode | undefined).find(Boolean);
      if (node) void selectFurniture(node);
    };
    const handleConnected = (event: THREE.Event & { target: THREE.Object3D; data: XRInputSource }) => {
      event.target.userData.handedness = event.data.handedness;
      event.target.children.forEach((child) => { child.visible = event.data.handedness === "right"; });
    };
    const handleDisconnected = (event: THREE.Event & { target: THREE.Object3D }) => {
      event.target.userData.handedness = undefined;
      event.target.children.forEach((child) => { child.visible = false; });
    };
    controllers.forEach((controller) => controller.addEventListener("select", handleSelect as never));
    controllers.forEach((controller) => controller.addEventListener("connected", handleConnected as never));
    controllers.forEach((controller) => controller.addEventListener("disconnected", handleDisconnected as never));

    const renderFrame = (time: number, frame?: XRFrame) => {
      const currentStage = stageRef.current;
      const dt = Math.min(0.05, (time - runtime.lastFrame) / 1000);
      runtime.lastFrame = time;
      const inputSources = inputSourcesByHand(xrSession.inputSources);
      const left = inputSources.left?.gamepad;
      const right = inputSources.right?.gamepad;
      if (left && left.axes.length >= 4 && currentStage) {
        const rawAxes = questLocomotionStick(left.axes[2] || 0, left.axes[3] || 0);
        const axes = rescaleThumbstick(rawAxes.x, rawAxes.y, DEAD_ZONE);
        const referenceSpace = renderer.xr.getReferenceSpace();
        const viewerPose = frame && referenceSpace
          ? frame.getViewerPose(referenceSpace)
          : null;
        const forward = viewerPose
          ? viewerForwardFromPose(
            viewerPose.transform.orientation,
            rig.getWorldQuaternion(new THREE.Quaternion()),
          )
          : (() => {
            const fallback = viewerPoseCamera(renderer.xr.getCamera())
              .getWorldDirection(new THREE.Vector3());
            fallback.y = 0;
            if (fallback.lengthSq() > 0) fallback.normalize();
            return fallback;
          })();
        const delta = headRelativeDelta(axes, forward, MOVE_SPEED, dt);
        const halfWidth = currentStage.room.width * SCALE / 2 - 0.25;
        const halfDepth = currentStage.room.depth * SCALE / 2 - 0.25;
        const nextX = THREE.MathUtils.clamp(rig.position.x + delta.x, -halfWidth, halfWidth);
        const nextZ = THREE.MathUtils.clamp(rig.position.z + delta.z, -halfDepth, halfDepth);
        const sceneToFloor = (x: number, z: number) => ({
          x: x / SCALE + currentStage.room.width / 2,
          y: currentStage.room.depth / 2 - z / SCALE,
        });
        if (isViewerPositionClear(sceneToFloor(nextX, nextZ), runtime.collisionNodes, currentStage.room)) {
          rig.position.x = nextX;
          rig.position.z = nextZ;
        } else if (isViewerPositionClear(sceneToFloor(nextX, rig.position.z), runtime.collisionNodes, currentStage.room)) {
          rig.position.x = nextX;
        } else if (isViewerPositionClear(sceneToFloor(rig.position.x, nextZ), runtime.collisionNodes, currentStage.room)) {
          rig.position.z = nextZ;
        }
      }
      if (right && right.axes.length >= 3) {
        const axis = right.axes[2] || 0;
        const turn = snapTurnStep(axis, runtime.turnArmed, SNAP_THRESHOLD, SNAP_REARM, SNAP_RADIANS);
        if (turn.delta) {
          const xrCamera = viewerPoseCamera(renderer.xr.getCamera());
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
        if (left.buttons.length > 4) {
          const pressed = left.buttons[4].pressed;
          if (pressedEdge(pressed, runtime.xPressed)) {
            const nextMicOn = !micRef.current;
            if (micRef.current) stopMicRef.current(); else void startMicRef.current();
            setMicStatus(`Microphone ${nextMicOn ? "on" : "off"}`);
            if (micStatusTimerRef.current !== null) window.clearTimeout(micStatusTimerRef.current);
            micStatusTimerRef.current = window.setTimeout(() => {
              setMicStatus(null);
              micStatusTimerRef.current = null;
            }, 1500);
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
        const visuals = runtime.avatar.userData.visuals as {
          body: THREE.Mesh;
          head: THREE.Mesh;
          leftArm: THREE.Mesh;
          rightArm: THREE.Mesh;
          chest: THREE.Mesh;
          halo: THREE.Mesh;
        };
        const moving = Boolean(runtime.avatar.userData.motion);
        const seated = runtime.avatar.userData.pose === "sitting";
        const pulse = Math.sin(time * 0.0026);
        const stride = Math.sin(time * 0.009);
        visuals.body.position.y = (seated ? 0.52 : 0.72) + pulse * 0.012;
        visuals.head.position.y = (seated ? 1.05 : 1.25) + pulse * 0.018;
        visuals.leftArm.rotation.x = moving ? stride * 0.42 : pulse * 0.035;
        visuals.rightArm.rotation.x = moving ? -stride * 0.42 : -pulse * 0.035;
        visuals.chest.scale.setScalar(1 + Math.max(0, pulse) * 0.08);
        visuals.halo.rotation.z = time * 0.00012;
        const headsetPosition = new THREE.Vector3();
        viewerPoseCamera(renderer.xr.getCamera()).getWorldPosition(headsetPosition);
        runtime.avatar.lookAt(headsetPosition.x, runtime.avatar.position.y, headsetPosition.z);
      }
      renderer.render(scene, camera);
    };
    renderer.setAnimationLoop(renderFrame);
    void renderer.xr.setSession(xrSession).then(() => renderer.xr.setFoveation(0.4));

    return () => {
      renderer.setAnimationLoop(null);
      controllers.forEach((controller) => controller.removeEventListener("select", handleSelect as never));
      controllers.forEach((controller) => controller.removeEventListener("connected", handleConnected as never));
      controllers.forEach((controller) => controller.removeEventListener("disconnected", handleDisconnected as never));
      disposeObject(scene);
      renderer.dispose();
      if (renderer.domElement.parentElement === host) host.removeChild(renderer.domElement);
      runtimeRef.current = null;
    };
  }, [xrSession]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime || !stage) return;
    const { furniture, scene } = runtime;
    while (furniture.children.length) {
      const child = furniture.children[furniture.children.length - 1];
      furniture.remove(child);
      disposeObject(child);
    }
    const loader = new THREE.TextureLoader();
    runtime.collisionNodes = stage.nodes;
    stage.nodes.filter((node) => node.type === "furniture").forEach((node) => {
      const solid = new THREE.MeshStandardMaterial({
        color: node.color, roughness: 0.64, metalness: 0.035,
      });
      const materials: THREE.Material[] = Array.from({ length: 6 }, () => solid.clone());
      solid.dispose();
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(node.width * SCALE, node.height * SCALE, node.depth * SCALE),
        materials,
      );
      const position = documentToScene(stage, node.x, node.y);
      mesh.position.set(position.x, furnitureCenterSceneY(node.elevation), position.z);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.userData.node = node;
      if (node.texture?.src) {
        loader.load(
          node.texture.src,
          (texture) => {
            if (mesh.parent !== furniture) {
              texture.dispose();
              return;
            }
            texture.colorSpace = THREE.SRGBColorSpace;
            texture.magFilter = THREE.LinearFilter;
            texture.minFilter = THREE.LinearMipmapLinearFilter;
            texture.anisotropy = Math.min(8, runtime.renderer.capabilities.getMaxAnisotropy());
            const image = texture.image as { width?: number; height?: number };
            const layouts = furnitureDecalLayouts(
              node,
              { width: image.width || 1, height: image.height || 1 },
            );
            layouts.forEach((layout) => {
              const decal = new THREE.Mesh(
                new THREE.PlaneGeometry(layout.width * SCALE, layout.height * SCALE),
                new THREE.MeshStandardMaterial({
                  map: texture,
                  transparent: true,
                  roughness: 0.55,
                  side: THREE.DoubleSide,
                  depthWrite: false,
                }),
              );
              decal.position.set(layout.x * SCALE, 0, layout.z * SCALE);
              decal.rotation.y = layout.rotationY;
              decal.renderOrder = 1;
              decal.userData.node = node;
              mesh.add(decal);
            });
          },
          undefined,
          () => {
            if (runtime.textureWarnings.has(node.id)) return;
            runtime.textureWarnings.add(node.id);
            setVrWarning(`Texture unavailable: ${node.label || node.id}`);
            if (warningTimerRef.current !== null) window.clearTimeout(warningTimerRef.current);
            warningTimerRef.current = window.setTimeout(() => {
              setVrWarning(null);
              warningTimerRef.current = null;
            }, 3000);
          },
        );
      }
      const edge = new THREE.LineSegments(
        new THREE.EdgesGeometry(mesh.geometry, 24),
        new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.1 }),
      );
      mesh.add(edge);
      mesh.traverse((child) => { child.userData.node = node; });
      furniture.add(mesh);
    });

    const roomWidth = stage.room.width * SCALE;
    const roomDepth = stage.room.depth * SCALE;
    const roomHeight = stage.room.height * SCALE;
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(roomWidth, roomDepth),
      new THREE.MeshStandardMaterial({
        color: stage.room.floor_color, roughness: 0.92, metalness: 0, side: THREE.DoubleSide,
      }),
    );
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    furniture.add(floor);
    const grid = new THREE.GridHelper(roomWidth, 24, 0xffffff, 0xffffff);
    grid.scale.z = roomDepth / roomWidth;
    grid.position.y = 0.004;
    const gridMaterials = Array.isArray(grid.material) ? grid.material : [grid.material];
    gridMaterials.forEach((material) => {
      material.transparent = true;
      material.opacity = 0.055;
      material.depthWrite = false;
    });
    furniture.add(grid);

    const wallMaterial = new THREE.MeshStandardMaterial({
      color: stage.room.wall_color, roughness: 0.88, metalness: 0, side: THREE.DoubleSide,
    });
    const back = new THREE.Mesh(new THREE.PlaneGeometry(roomWidth, roomHeight), wallMaterial);
    back.position.set(0, roomHeight / 2, roomDepth / 2);
    back.rotation.y = Math.PI;
    back.receiveShadow = true;
    furniture.add(back);
    const front = back.clone(); front.position.z = -roomDepth / 2; front.rotation.y = 0; furniture.add(front);
    const sideGeometry = new THREE.PlaneGeometry(roomDepth, roomHeight);
    const leftWall = new THREE.Mesh(sideGeometry, wallMaterial.clone());
    leftWall.position.set(-roomWidth / 2, roomHeight / 2, 0); leftWall.rotation.y = Math.PI / 2; furniture.add(leftWall);
    const rightWall = leftWall.clone(); rightWall.position.x = roomWidth / 2; rightWall.rotation.y = -Math.PI / 2; furniture.add(rightWall);
    const trimMaterial = new THREE.MeshStandardMaterial({ color: 0xf5ead9, roughness: 0.7 });
    const horizontalTrim = new THREE.BoxGeometry(roomWidth, 0.07, 0.06);
    const verticalTrim = new THREE.BoxGeometry(0.06, 0.07, roomDepth);
    [
      new THREE.Mesh(horizontalTrim, trimMaterial),
      new THREE.Mesh(horizontalTrim, trimMaterial.clone()),
    ].forEach((trim, index) => {
      trim.position.set(0, 0.035, index === 0 ? -roomDepth / 2 + 0.03 : roomDepth / 2 - 0.03);
      trim.castShadow = true;
      furniture.add(trim);
    });
    [
      new THREE.Mesh(verticalTrim, trimMaterial.clone()),
      new THREE.Mesh(verticalTrim, trimMaterial.clone()),
    ].forEach((trim, index) => {
      trim.position.set(index === 0 ? -roomWidth / 2 + 0.03 : roomWidth / 2 - 0.03, 0.035, 0);
      trim.castShadow = true;
      furniture.add(trim);
    });

    const avatarNode = stage.nodes.find((node) => node.id === "avatar");
    if (avatarNode) {
      if (!runtime.avatar) {
        runtime.avatar = createSpatialAvatar(avatarNode);
        scene.add(runtime.avatar);
      }
      const target = documentToScene(stage, avatarNode.x, avatarNode.y);
      if (runtime.avatar.position.lengthSq() === 0) runtime.avatar.position.copy(target);
      else if (!runtime.avatar.position.equals(target)) {
        runtime.avatar.userData.motion = {
          from: runtime.avatar.position.clone(), to: target, start: performance.now(), duration: motionDurationMs,
        };
      }
      runtime.avatar.userData.pose = avatarNode.pose;
    }
    if (!runtime.rig.userData.spawned) {
      const spawnFloor = avatarNode
        ? selectViewerSpawn(
          avatarNode,
          stage.nodes.filter((node) => node.type === "furniture"),
          stage.room,
        )
        : { x: stage.room.width / 2, y: stage.room.depth / 2 };
      const spawn = documentToScene(stage, spawnFloor.x, spawnFloor.y);
      runtime.rig.position.set(spawn.x, 0, spawn.z);
      if (avatarNode) {
        const companion = documentToScene(stage, avatarNode.x, avatarNode.y);
        runtime.rig.rotation.y = Math.atan2(-(companion.x - spawn.x), -(companion.z - spawn.z));
      }
      runtime.rig.userData.spawned = true;
    }
  }, [stage, motionDurationMs]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    const activityText = activity.phase === "idle" ? "" : activity.phase.replace(/-/g, " ");
    const shouldShow = Boolean(subtitleText.trim() || activityText || micStatus || vrWarning || audioWarning || wsState !== "OPEN");
    runtime.hud.visible = shouldShow;
    if (!shouldShow) return;
    const status = [
      wsState === "OPEN" ? "Connected" : wsState.toLowerCase(),
      activityText,
      micStatus,
      vrWarning,
      audioWarning,
    ].filter(Boolean).join(" · ");
    const caption = audioWarning || subtitleText.trim() || vrWarning || micStatus || activityText || "Reconnecting to your companion";
    const next = captionTexture(caption, status);
    (runtime.hud.material as THREE.MeshBasicMaterial).map = next;
    runtime.hudTexture.dispose();
    runtime.hudTexture = next;
  }, [subtitleText, activity, micStatus, vrWarning, audioWarning, wsState]);

  return (
    <>
      <div ref={hostRef} style={{ position: "fixed", inset: 0, background: "#0c1120" }} />
      <Button
        position="fixed"
        top="max(18px, env(safe-area-inset-top))"
        right="max(18px, env(safe-area-inset-right))"
        zIndex="10"
        height="38px"
        px="4"
        gap="2"
        color="#e2e8f0"
        bg="rgba(8, 12, 24, 0.72)"
        border="1px solid rgba(255,255,255,0.16)"
        borderRadius="full"
        backdropFilter="blur(20px)"
        _hover={{ bg: "rgba(15, 23, 42, 0.9)" }}
        onClick={() => void leaveVr()}
        aria-label="Exit immersive VR"
      >
        <FiX />
        Exit VR
      </Button>
    </>
  );
}
