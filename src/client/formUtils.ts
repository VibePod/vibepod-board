export const parseListField = (value: string): string[] =>
  [...new Set(value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean))];

export const formatListField = (items: string[]): string => items.join("\n");
