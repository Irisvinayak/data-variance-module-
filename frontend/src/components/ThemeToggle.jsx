import { useTheme } from "../context/ThemeContext";

function ThemeToggle() {
    const { theme, toggleTheme } = useTheme();

    return (
        <button onClick={toggleTheme}>
            {theme === "light" ? "🌙 Dark" : "☀️ Light"}
        </button>
    );
}

export default ThemeToggle;
