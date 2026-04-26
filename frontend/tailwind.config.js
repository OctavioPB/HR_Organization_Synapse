/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: "#003366",
        "primary-80": "#1A4D80",
        "primary-60": "#336699",
        "primary-30": "#99BBDD",
        "primary-10": "#E0EAF4",
        gold: "#C8982A",
        "gold-light": "#E8C46A",
        dark: "#1C1C2E",
        mid: "#6B7280",
        light: "#F4F6F9",
      },
      fontFamily: {
        display: ["Fraunces", "Georgia", "serif"],
        body: ["Plus Jakarta Sans", "sans-serif"],
      },
    },
  },
  plugins: [],
};
