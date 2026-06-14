/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        plum: { 900: "#11040d", 800: "#2c0b21", 700: "#460932" },
        coral: "#ff4052",
        coraldark: "#2c0b21",
        cream: "#fffaf2",
        creamtext: "#fff1e5",
        growth: "#92bd33",
        sun: "#ffbf21",
        crimson: "#cc3342",
        sky: "#1d9bf0",
        molten: "#ff5600",
        // Darker text-only variants for small pill labels (WCAG AA on light pill bgs).
        growthText: "#4d7000",
        skyText: "#0c6db3",
        moltenText: "#cc3300",
        sunText: "#946b00",
      },
      fontFamily: {
        serif: ["Fraunces", "Georgia", "serif"],
        sans: ["Inter", "Arial", "sans-serif"],
      },
      borderRadius: {
        card: "1rem",
      },
      maxWidth: {
        content: "90rem",
      },
    },
  },
  plugins: [],
};
