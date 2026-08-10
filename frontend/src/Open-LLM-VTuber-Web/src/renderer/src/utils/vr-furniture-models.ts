import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

export interface FurnitureModelAsset {
  src: string;
  accessory?: {
    src: string;
    rotationY?: number;
    widthFraction: number;
  };
  keepFrontTexture?: boolean;
  rotationY?: number;
}

export interface ModelBounds {
  min: { x: number; y: number; z: number };
  max: { x: number; y: number; z: number };
}

export interface ModelTargetSize {
  width: number;
  height: number;
  depth: number;
}

export interface ModelFitTransform {
  scale: { x: number; y: number; z: number };
  position: { x: number; y: number; z: number };
}

const VR_MODEL_ROOT = "./stage-assets/vr-models";
const wallCorkboard: FurnitureModelAsset = {
  src: `${VR_MODEL_ROOT}/wall-corkboard.glb`,
  keepFrontTexture: true,
};

export const FURNITURE_MODEL_ASSETS: Readonly<
  Record<string, FurnitureModelAsset>
> = {
  archive_shelf: { src: `${VR_MODEL_ROOT}/archive-shelf.glb`, rotationY: Math.PI },
  bed: { src: `${VR_MODEL_ROOT}/bed.glb`, rotationY: Math.PI / 2 },
  bookshelf: { src: `${VR_MODEL_ROOT}/bookshelf.glb`, rotationY: Math.PI },
  calendar: wallCorkboard,
  computer: {
    src: `${VR_MODEL_ROOT}/writing-desk.glb`,
    accessory: {
      src: `${VR_MODEL_ROOT}/computer.glb`,
      rotationY: Math.PI / 2,
      widthFraction: 0.3,
    },
  },
  design_board: wallCorkboard,
  drawing_board: wallCorkboard,
  mailbox: { src: `${VR_MODEL_ROOT}/mailbox.glb`, rotationY: Math.PI / 2 },
  reading_chair: { src: `${VR_MODEL_ROOT}/reading-chair.glb` },
  sofa: { src: `${VR_MODEL_ROOT}/sofa.glb` },
  todo_list: wallCorkboard,
  window: { src: `${VR_MODEL_ROOT}/window.glb` },
  writing_desk: { src: `${VR_MODEL_ROOT}/writing-desk.glb` },
};

export function furnitureModelAsset(nodeId: string) {
  return FURNITURE_MODEL_ASSETS[nodeId];
}

export function furnitureModelFit(
  bounds: ModelBounds,
  target: ModelTargetSize,
): ModelFitTransform | null {
  const source = {
    x: bounds.max.x - bounds.min.x,
    y: bounds.max.y - bounds.min.y,
    z: bounds.max.z - bounds.min.z,
  };
  if (
    ![
      source.x,
      source.y,
      source.z,
      target.width,
      target.height,
      target.depth,
    ].every((value) => Number.isFinite(value) && value > 0)
  )
    return null;

  const center = {
    x: (bounds.min.x + bounds.max.x) / 2,
    y: (bounds.min.y + bounds.max.y) / 2,
    z: (bounds.min.z + bounds.max.z) / 2,
  };
  const scale = {
    x: target.width / source.x,
    y: target.height / source.y,
    z: target.depth / source.z,
  };
  const centredPosition = {
    x: -center.x * scale.x,
    y: -center.y * scale.y,
    z: -center.z * scale.z,
  };
  return {
    scale,
    position: {
      x: centredPosition.x || 0,
      y: centredPosition.y || 0,
      z: centredPosition.z || 0,
    },
  };
}

const loader = new GLTFLoader();
const modelTemplates = new Map<string, Promise<THREE.Group>>();

function cloneTextureProperties(material: THREE.Material) {
  const clone = material.clone();
  const sourceValues = material as unknown as Record<string, unknown>;
  const cloneValues = clone as unknown as Record<string, unknown>;
  Object.entries(sourceValues).forEach(([key, value]) => {
    if (!(value instanceof THREE.Texture)) return;
    const texture = value.clone();
    texture.needsUpdate = true;
    cloneValues[key] = texture;
  });
  return clone;
}

function cloneModel(template: THREE.Group) {
  const clone = template.clone(true);
  clone.traverse((object) => {
    if (!(object instanceof THREE.Mesh)) return;
    object.geometry = object.geometry.clone();
    object.material = Array.isArray(object.material)
      ? object.material.map(cloneTextureProperties)
      : cloneTextureProperties(object.material);
    object.castShadow = true;
    object.receiveShadow = true;
  });
  return clone;
}

async function modelTemplate(src: string) {
  let pending = modelTemplates.get(src);
  if (!pending) {
    pending = loader
      .loadAsync(src)
      .then((gltf) => gltf.scene)
      .catch((error) => {
        modelTemplates.delete(src);
        throw error;
      });
    modelTemplates.set(src, pending);
  }
  return pending;
}

export async function loadFurnitureModel(asset: FurnitureModelAsset) {
  if (!asset.accessory) return cloneModel(await modelTemplate(asset.src));

  const [modelSource, accessorySource] = await Promise.all([
    modelTemplate(asset.src),
    modelTemplate(asset.accessory.src),
  ]);
  const model = cloneModel(modelSource);
  const accessory = cloneModel(accessorySource);
  accessory.rotation.y = asset.accessory.rotationY || 0;
  accessory.updateMatrixWorld(true);

  const modelBox = new THREE.Box3().setFromObject(model);
  const modelSize = modelBox.getSize(new THREE.Vector3());
  const modelCenter = modelBox.getCenter(new THREE.Vector3());
  const accessoryBox = new THREE.Box3().setFromObject(accessory);
  const accessorySize = accessoryBox.getSize(new THREE.Vector3());
  const scale = (modelSize.x * asset.accessory.widthFraction) / accessorySize.x;
  accessory.scale.setScalar(scale);
  accessory.updateMatrixWorld(true);

  const scaledBox = new THREE.Box3().setFromObject(accessory);
  const scaledCenter = scaledBox.getCenter(new THREE.Vector3());
  accessory.position.add(
    new THREE.Vector3(
      modelCenter.x - scaledCenter.x,
      modelBox.max.y - scaledBox.min.y,
      modelCenter.z - scaledCenter.z,
    ),
  );

  const composite = new THREE.Group();
  composite.add(model, accessory);
  return composite;
}

export function fitFurnitureModel(
  model: THREE.Object3D,
  target: ModelTargetSize,
) {
  const box = new THREE.Box3().setFromObject(model);
  const fit = furnitureModelFit(
    {
      min: { x: box.min.x, y: box.min.y, z: box.min.z },
      max: { x: box.max.x, y: box.max.y, z: box.max.z },
    },
    target,
  );
  if (!fit) return false;
  model.scale.set(fit.scale.x, fit.scale.y, fit.scale.z);
  model.position.set(fit.position.x, fit.position.y, fit.position.z);
  return true;
}
