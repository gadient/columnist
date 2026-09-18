export const generateInitials = (name) => {
  const words = name.trim().split(' ').filter(word => word.length > 0);

  if (words.length === 0) return '';
  if (words.length === 1) {
    return words[0].slice(0, 2).toUpperCase();
  }
  return words.slice(0, 2).map(word => word.charAt(0)).join('').toUpperCase();
};
