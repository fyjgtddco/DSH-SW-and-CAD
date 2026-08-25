;;; ================================================================
;;; CADX.LSP â€” AutoLISP commands for CADX AI-Driven CAD Validator
;;; Load in AutoCAD: (load "C:/Users/reddy/OneDrive/Desktop/CADX/cadx.lsp")
;;; Commands: CADX, CADXFIX, CADXCLEAR, CADXDESIGN
;;; ================================================================

;;; ------------------------------------------------------------------
;;; Helper: Call CADX backend API
;;; ------------------------------------------------------------------

(defun cadx-api-call (endpoint method data / url cmd result)
  (setq url (strcat "http://localhost:8001" endpoint))
  ;; Use PowerShell to make HTTP calls (portable on Windows)
  (if (= method "GET")
    (setq cmd (strcat "powershell -Command \"(Invoke-RestMethod -Uri '" url "' -Method Get) | ConvertTo-Json\""))
    (setq cmd (strcat "powershell -Command \"Invoke-RestMethod -Uri '" url 
                      "' -Method Post -ContentType 'application/json' -Body '"
                      data "' | ConvertTo-Json\""))
  )
  (startapp "cmd" (strcat "/C " cmd))
  (princ "\n[CADX] Request sent to backend.")
)

;;; ------------------------------------------------------------------
;;; CADX â€” Validate current drawing
;;; ------------------------------------------------------------------

(defun c:CADX (/ dxfpath)
  (princ "\n[CADX] Starting design validation...")
  (princ "\n[CADX] Exporting DXF for analysis...")
  
  ;; Save current drawing as DXF to temp location
  (setq dxfpath (strcat (getenv "TEMP") "\\cadx_validate.dxf"))
  (command "._DXFOUT" dxfpath "V" "R2018" "16" "")
  
  (princ (strcat "\n[CADX] DXF exported to: " dxfpath))
  (princ "\n[CADX] Sending to validation engine...")
  (princ "\n[CADX] Open CADX dashboard at http://localhost:8501 for results.")
  (princ "\n[CADX] Or run validation via: http://localhost:8001/validate")
  
  ;; Trigger validation via API
  (cadx-api-call "/validate" "POST" 
    (strcat "{\"dxf_path\":\"" (vl-string-translate "\\" "/" dxfpath) "\",\"rules_file\":\"residential.json\"}"))
  
  (princ "\n[CADX] Validation triggered. Check dashboard for results.")
  (princ)
)

;;; ------------------------------------------------------------------
;;; CADXFIX â€” Fix all issues found by validation
;;; ------------------------------------------------------------------

(defun c:CADXFIX ()
  (princ "\n[CADX] Requesting AI fixes for all issues...")
  (cadx-api-call "/fix-all" "POST" "{}")
  (princ "\n[CADX] Fix commands sent. Check AutoCAD for changes.")
  (command "._REGEN")
  (princ)
)

;;; ------------------------------------------------------------------
;;; CADXCLEAR â€” Remove all validation markers
;;; ------------------------------------------------------------------

(defun c:CADXCLEAR ()
  (princ "\n[CADX] Clearing validation markers...")
  (command "._-LAYDEL" "N" "CADX_VALIDATION" "" "Y")
  (command "._REGEN")
  (princ "\n[CADX] Validation layer cleared.")
  (princ)
)

;;; ------------------------------------------------------------------
;;; CADXDESIGN â€” Generate a design via AI prompt
;;; ------------------------------------------------------------------

(defun c:CADXDESIGN (/ prompt)
  (setq prompt (getstring T "\n[CADX] Describe the design: "))
  (if (= prompt "")
    (progn
      (princ "\n[CADX] No prompt provided. Cancelled.")
      (princ)
    )
    (progn
      (princ (strcat "\n[CADX] Generating design: " prompt))
      (cadx-api-call "/design" "POST" 
        (strcat "{\"prompt\":\"" prompt "\"}"))
      (princ "\n[CADX] Design request sent. Check AutoCAD for new entities.")
      (princ)
    )
  )
)

;;; ------------------------------------------------------------------
;;; CADXCHAT â€” Ask the AI a question
;;; ------------------------------------------------------------------

(defun c:CADXCHAT (/ msg)
  (setq msg (getstring T "\n[CADX] Ask AI: "))
  (if (/= msg "")
    (progn
      (cadx-api-call "/chat" "POST" 
        (strcat "{\"message\":\"" msg "\"}"))
      (princ "\n[CADX] Response sent to dashboard.")
    )
  )
  (princ)
)

;;; ------------------------------------------------------------------
;;; Auto-load message
;;; ------------------------------------------------------------------

(princ "\n")
(princ "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
(princ "\n  CADX â€” AI-Driven CAD Validation Agent")
(princ "\nâ”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
(princ "\n  Commands:")
(princ "\n    CADX        â€” Validate current drawing")
(princ "\n    CADXFIX     â€” AI fix all issues")
(princ "\n    CADXCLEAR   â€” Remove validation markers")
(princ "\n    CADXDESIGN  â€” Generate design from prompt")
(princ "\n    CADXCHAT    â€” Ask the AI a question")
(princ "\n")
(princ "\n  Dashboard: http://localhost:8501")
(princ "\n  API:       http://localhost:8001/docs")
(princ "\nâ”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
(princ)
