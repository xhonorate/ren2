import { createContext, memo, useContext } from "react";
import { BaseEdge, getBezierPath, Position, type EdgeProps } from "reactflow";
import { labelPoint, routeDown, roundedPath, type Box } from "../lib/route";

/** The cards on the canvas, for edges to route around. Provided by Canvas
 *  from saved positions, so it holds still while a card is being dragged
 *  instead of re-routing every edge on every frame. */
export const ObstacleContext = createContext<Box[]>([]);

function FlowEdge(props: EdgeProps<{ turn?: number }>) {
  const {
    id, source, target, sourceX, sourceY, targetX, targetY, data,
    sourcePosition, targetPosition, label, style, markerEnd, markerStart,
    labelStyle, labelShowBg, labelBgStyle, labelBgPadding, labelBgBorderRadius,
    interactionWidth,
  } = props;
  const obstacles = useContext(ObstacleContext);

  const points = sourcePosition === Position.Bottom && targetPosition === Position.Top
    ? routeDown({ x: sourceX, y: sourceY }, { x: targetX, y: targetY }, obstacles,
      [source, target], data?.turn ?? 0)
    : null;

  let path: string;
  let labelX: number;
  let labelY: number;
  if (points) {
    path = roundedPath(points);
    ({ x: labelX, y: labelY } = labelPoint(points));
  } else {
    [path, labelX, labelY] = getBezierPath({
      sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition,
    });
  }

  return (
    <BaseEdge
      id={id}
      path={path}
      labelX={labelX}
      labelY={labelY}
      label={label}
      labelStyle={labelStyle}
      labelShowBg={labelShowBg}
      labelBgStyle={labelBgStyle}
      labelBgPadding={labelBgPadding}
      labelBgBorderRadius={labelBgBorderRadius}
      style={style}
      markerEnd={markerEnd}
      markerStart={markerStart}
      interactionWidth={interactionWidth}
    />
  );
}

export default memo(FlowEdge);
