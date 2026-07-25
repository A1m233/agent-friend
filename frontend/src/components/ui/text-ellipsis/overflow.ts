type OverflowMeasurable = Pick<HTMLElement, "clientWidth" | "scrollWidth">;

function isTextOverflowing(element: OverflowMeasurable): boolean {
  return element.scrollWidth - element.clientWidth > 1;
}

function observeTextOverflow(
  element: HTMLElement,
  onChange: (isOverflowing: boolean) => void,
): () => void {
  const measure = () => onChange(isTextOverflowing(element));
  const observer = new ResizeObserver(measure);

  measure();
  observer.observe(element);

  return () => observer.disconnect();
}

export { isTextOverflowing, observeTextOverflow };
