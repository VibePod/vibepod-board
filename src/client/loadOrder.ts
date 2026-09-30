/**
 * Orders overlapping loads: each load takes a ticket when it starts, and its
 * result may be applied unless a newer load's result already was. A newer load
 * that fails leaves an older one free to apply, so a successful load is never
 * dropped in favour of one that produced nothing.
 */
export const createLoadOrder = () => {
  let issued = 0;
  let applied = 0;
  return {
    start: () => ++issued,
    /** Whether the load with this ticket may apply its result; marks it applied. */
    apply: (ticket: number) => {
      if (ticket < applied) return false;
      applied = ticket;
      return true;
    },
  };
};
