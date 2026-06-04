import { useEffect, useState } from "react";
import { readToken, setToken } from "../api/client";

export function useAuthToken() {
  const [token, setTokenState] = useState("");

  useEffect(() => {
    setTokenState(readToken());
  }, []);

  function updateToken(value: string) {
    setToken(value);
    setTokenState(value);
  }

  return { token, setToken: updateToken };
}
