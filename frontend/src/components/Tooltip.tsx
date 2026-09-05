import React, { useState, useRef, useEffect, type ReactNode, type ReactElement } from "react";
import { createPortal } from "react-dom";

type TooltipProps = {
  content?: ReactNode;
  children: ReactElement;
  placement?: "top" | "bottom";
  className?: string;
};

export function Tooltip({ content, children, placement = "top", className = "" }: TooltipProps) {
  const [visible, setVisible] = useState(false);
  const [coords, setCoords] = useState<{ top: number; left: number }>({ top: 0, left: 0 });
  const triggerRef = useRef<HTMLElement | null>(null);

  const calculatePosition = () => {
    if (!triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const scrollX = window.scrollX || window.pageXOffset;
    const scrollY = window.scrollY || window.pageYOffset;

    if (placement === "top") {
      setCoords({
        top: rect.top + scrollY - 8,
        left: rect.left + scrollX + rect.width / 2
      });
    } else {
      setCoords({
        top: rect.bottom + scrollY + 8,
        left: rect.left + scrollX + rect.width / 2
      });
    }
  };

  const handleMouseEnter = () => {
    if (!content) return;
    calculatePosition();
    setVisible(true);
  };

  const handleMouseLeave = () => {
    setVisible(false);
  };

  useEffect(() => {
    if (!visible) return;
    const handleScrollOrResize = () => {
      calculatePosition();
    };
    window.addEventListener("scroll", handleScrollOrResize, true);
    window.addEventListener("resize", handleScrollOrResize);
    return () => {
      window.removeEventListener("scroll", handleScrollOrResize, true);
      window.removeEventListener("resize", handleScrollOrResize);
    };
  }, [visible]);

  // 克隆子元素并绑定事件与 ref
  const child = React.cloneElement(children, {
    ref: (node: HTMLElement | null) => {
      triggerRef.current = node;
      // 保留原有 ref
      const origRef = (children as any).ref;
      if (typeof origRef === "function") origRef(node);
      else if (origRef && typeof origRef === "object") origRef.current = node;
    },
    onMouseEnter: (e: React.MouseEvent) => {
      handleMouseEnter();
      children.props.onMouseEnter?.(e);
    },
    onMouseLeave: (e: React.MouseEvent) => {
      handleMouseLeave();
      children.props.onMouseLeave?.(e);
    }
  });

  return (
    <>
      {child}
      {visible && content && typeof document !== "undefined"
        ? createPortal(
            <div
              className={`custom-tooltip-portal ${placement} ${className}`}
              style={{
                position: "absolute",
                top: coords.top,
                left: coords.left,
                transform: placement === "top" ? "translate(-50%, -100%)" : "translate(-50%, 0)",
                pointerEvents: "none",
                zIndex: 99999
              }}
              role="tooltip"
            >
              <div className="custom-tooltip-content">{content}</div>
              <div className={`custom-tooltip-arrow ${placement}`} />
            </div>,
            document.body
          )
        : null}
    </>
  );
}
