import "./components.css";

interface ToggleSwitchProps {
  checked: boolean;
  onChange?: (checked: boolean) => void;
  label?: string;
}

export function ToggleSwitch({ checked, onChange, label }: ToggleSwitchProps) {
  return (
    <button
      type="button"
      className={`toggle ${checked ? "toggle--on" : ""}`}
      aria-pressed={checked}
      onClick={() => onChange?.(!checked)}
      title={label}
    >
      <span />
    </button>
  );
}
