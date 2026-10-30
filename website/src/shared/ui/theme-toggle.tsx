"use client";

import { MoonIcon, SunIcon } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { Button } from "@/shared/ui/button";

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  // Avoid a hydration mismatch: the resolved theme is unknown on the server.
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  const isDark = mounted && resolvedTheme === "dark";
  const label = isDark ? "切换到浅色" : "切换到深色";
  const Icon = isDark ? SunIcon : MoonIcon;

  return (
    <Button
      variant="ghost"
      size="icon"
      className="size-11 text-muted-foreground"
      aria-label={label}
      title={label}
      onClick={() => setTheme(isDark ? "light" : "dark")}
    >
      <Icon aria-hidden="true" className="size-[18px]" />
    </Button>
  );
}
