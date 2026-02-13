/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/apps/**/templates/**/*.html", "./app/static/js/**/*.js"],
  theme: {
    extend: {
      fontFamily: {
        display: ["\"Inter\"", "\"Noto Sans SC\"", "\"PingFang SC\"", "\"Microsoft YaHei\"", "sans-serif"],
        body: ["\"Inter\"", "\"Noto Sans SC\"", "\"PingFang SC\"", "\"Microsoft YaHei\"", "sans-serif"],
      },
      boxShadow: {
        ink: "0 8px 24px rgba(15, 23, 42, 0.12)",
        glow: "0 0 0 1px rgba(22, 119, 255, 0.24), 0 6px 18px rgba(22, 119, 255, 0.2)",
      },
      borderRadius: {
        blob: "2.25rem",
      },
    },
  },
  plugins: [],
};
