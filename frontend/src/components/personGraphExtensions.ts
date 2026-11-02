// G6's root imports the full preset (layouts, plugins, nodes and behaviors).
// Keep this graph's preset inside its lazy chunk, with the same core defaults.
import { register } from "@antv/g6/esm/registry/register";
import { Circle } from "@antv/g6/esm/elements/nodes/circle";
import { Quadratic } from "@antv/g6/esm/elements/edges/quadratic";
import { DragCanvas } from "@antv/g6/esm/behaviors/drag-canvas";
import { DragElement } from "@antv/g6/esm/behaviors/drag-element";
import { ZoomCanvas } from "@antv/g6/esm/behaviors/zoom-canvas";
import { ScrollCanvas } from "@antv/g6/esm/behaviors/scroll-canvas";
import { ArrangeDrawOrder } from "@antv/g6/esm/transforms/arrange-draw-order";
import { CollapseExpandCombo } from "@antv/g6/esm/transforms/collapse-expand-combo";
import { CollapseExpandNode } from "@antv/g6/esm/transforms/collapse-expand-node";
import { GetEdgeActualEnds } from "@antv/g6/esm/transforms/get-edge-actual-ends";
import { UpdateRelatedEdge } from "@antv/g6/esm/transforms/update-related-edge";
import { dark, light } from "@antv/g6/esm/themes";
import { ComboCollapse, ComboExpand, Fade, NodeCollapse, NodeExpand, PathIn, PathOut, Translate } from "@antv/g6/esm/animations";
import { Circle as GCircle, Ellipse, Group, HTML, Line, Path, Polygon, Polyline, Rect, Text } from "@antv/g";
import { Badge } from "@antv/g6/esm/elements/shapes/badge";
import { Image } from "@antv/g6/esm/elements/shapes/image";
import { Label } from "@antv/g6/esm/elements/shapes/label";

register("node", "circle", Circle);
register("edge", "quadratic", Quadratic);
register("behavior", "drag-element", DragElement);
register("behavior", "drag-canvas", DragCanvas);
register("behavior", "zoom-canvas", ZoomCanvas);
register("behavior", "scroll-canvas", ScrollCanvas);
// These five transforms are required by G6 even without explicit transforms.
register("transform", "update-related-edges", UpdateRelatedEdge);
register("transform", "collapse-expand-node", CollapseExpandNode);
register("transform", "collapse-expand-combo", CollapseExpandCombo);
register("transform", "get-edge-actual-ends", GetEdgeActualEnds);
register("transform", "arrange-draw-order", ArrangeDrawOrder);
register("theme", "light", light);
register("theme", "dark", dark);
for (const [name, animation] of Object.entries({
  "combo-collapse": ComboCollapse, "combo-expand": ComboExpand,
  "node-collapse": NodeCollapse, "node-expand": NodeExpand,
  "path-in": PathIn, "path-out": PathOut, fade: Fade, translate: Translate,
})) register("animation", name, animation);
for (const [name, shape] of Object.entries({
  circle: GCircle, ellipse: Ellipse, group: Group, html: HTML, image: Image,
  line: Line, path: Path, polygon: Polygon, polyline: Polyline, rect: Rect,
  text: Text, label: Label, badge: Badge,
})) register("shape", name, shape);
