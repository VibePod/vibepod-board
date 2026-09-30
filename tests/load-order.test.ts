import { describe, expect, it } from "vitest";
import { createLoadOrder } from "../src/client/loadOrder.js";

describe("load order", () => {
  it("drops an older load that finishes after a newer one was applied", () => {
    const order = createLoadOrder();
    const older = order.start();
    const newer = order.start();

    expect(order.apply(newer)).toBe(true);
    expect(order.apply(older)).toBe(false);
  });

  it("applies an older load when the newer one failed", () => {
    const order = createLoadOrder();
    const older = order.start();
    order.start(); // fails and never applies

    expect(order.apply(older)).toBe(true);
  });

  it("applies loads that finish in the order they started", () => {
    const order = createLoadOrder();
    const first = order.start();
    const second = order.start();

    expect(order.apply(first)).toBe(true);
    expect(order.apply(second)).toBe(true);
  });
});
