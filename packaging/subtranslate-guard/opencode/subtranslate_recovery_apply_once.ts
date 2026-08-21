/** Uninstalled structured-tool source. ASK is secondary confirmation only. */
export const TOOL_ID = "subtranslate_recovery_apply_once"
export const args = {}
const REQUEST = "EXECUTE_CURRENT_ARMED_RECOVERY_CAPABILITY\n"
const SOCKET_PATH = "/run/subtranslate-guard/guard.sock"

export async function execute(arguments: Record<string, never>, ask: () => Promise<void>, sendFixed: (path: string, request: string, halfCloseWrite: true, maxResponseBytes: number) => Promise<string>) {
  if (Object.keys(arguments).length !== 0) throw new Error("ZERO_ARGUMENT_CONTRACT_VIOLATION")
  await ask() // Secondary human confirmation; never the one-shot barrier.
  return sendFixed(SOCKET_PATH, REQUEST, true, 4096)
}
