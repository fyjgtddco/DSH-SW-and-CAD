# Modify dsh-engineering-ui client.js for 3-column layout
$filePath = "C:\Users\j1877\.dsh\profiles\web\node_modules\dsh-engineering-ui\lib\client.js"
$content = [System.IO.File]::ReadAllText($filePath, [System.Text.Encoding]::UTF8)
Write-Host "Read $($content.Length) chars"

# Show exact context around eng-dock CSS
$idx = $content.IndexOf(".eng-dock{background:#ffffff")
Write-Host "eng-dock index: $idx"
if ($idx -gt 0) {
    Write-Host "Before: [$($content.Substring([Math]::Max(0,$idx-40), 40))]"
    Write-Host "At: [$($content.Substring($idx, 200))]"
}

# Show exact context around eng-rail
$idx2 = $content.IndexOf(".eng-rail{background:#f1f5f9")
Write-Host "eng-rail index: $idx2"
if ($idx2 -gt 0) {
    Write-Host "Before: [$($content.Substring([Math]::Max(0,$idx2-40), 40))]"
    Write-Host "At: [$($content.Substring($idx2, 200))]"
}

# Show eng-screen context
$idx3 = $content.IndexOf(".eng-screen{background:#ffffff;display:flex;flex:1;flex-direction:column;min-width:0}")
Write-Host "eng-screen index: $idx3"

# Show dark mode context
$idx4 = $content.IndexOf(".eng-dock{background:#16181d")
Write-Host "dark eng-dock index: $idx4"

# Show media query
$idx5 = $content.IndexOf("@media(max-width:900px)")
Write-Host "media query index: $idx5"
if ($idx5 -ge 0) {
    Write-Host "Media: [$($content.Substring($idx5, 120))]"
}

Write-Host "Done analyzing"
