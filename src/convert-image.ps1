param(
  [Parameter(Mandatory = $true)][string]$InputPath,
  [Parameter(Mandatory = $true)][string]$OutputPath,
  [int]$MaxSide = 1200,
  [int]$Quality = 78
)

Add-Type -AssemblyName System.Drawing

$image = [System.Drawing.Image]::FromFile($InputPath)
try {
  $width = [double]$image.Width
  $height = [double]$image.Height
  $scale = [Math]::Min(1.0, $MaxSide / [Math]::Max($width, $height))
  $newWidth = [Math]::Max(1, [int][Math]::Round($width * $scale))
  $newHeight = [Math]::Max(1, [int][Math]::Round($height * $scale))

  $bitmap = New-Object System.Drawing.Bitmap $newWidth, $newHeight
  try {
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
      $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
      $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
      $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
      $graphics.DrawImage($image, 0, 0, $newWidth, $newHeight)

      $jpegCodec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() |
        Where-Object { $_.MimeType -eq "image/jpeg" } |
        Select-Object -First 1
      $encoder = [System.Drawing.Imaging.Encoder]::Quality
      $encoderParams = New-Object System.Drawing.Imaging.EncoderParameters 1
      $encoderParams.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter $encoder, ([int64]$Quality)

      $directory = Split-Path -Parent $OutputPath
      if ($directory -and -not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory | Out-Null
      }
      $bitmap.Save($OutputPath, $jpegCodec, $encoderParams)
    } finally {
      $graphics.Dispose()
    }
  } finally {
    $bitmap.Dispose()
  }
} finally {
  $image.Dispose()
}
