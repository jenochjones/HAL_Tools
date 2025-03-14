from PIL import Image
import os

def convert_heic_to_jpg(heic_folder, jpg_folder):
    # Normalize paths to handle any OS-specific quirks
    heic_folder = os.path.normpath(heic_folder)
    jpg_folder = os.path.normpath(jpg_folder)

    # Create the output folder if it doesn't exist
    if not os.path.exists(jpg_folder):
        os.makedirs(jpg_folder)

    # Loop through all files in the HEIC folder
    for filename in os.listdir(heic_folder):
        if filename.lower().endswith('.heic'):
            # Open the HEIC file
            heic_path = os.path.join(heic_folder, filename)
            with Image.open(heic_path) as img:
                # Extract EXIF metadata
                exif = img.info.get('exif')

                # Convert and save as JPG
                jpg_filename = os.path.splitext(filename)[0] + '.jpg'
                jpg_path = os.path.join(jpg_folder, jpg_filename)
                img.convert("RGB").save(jpg_path, "JPEG", exif=exif)


if __name__ == "__main__":
    heic_folder = os.path.join('.', 'HEIC')
    jpg_folder = os.path.join('.', 'JPG')
    convert_heic_to_jpg(heic_folder, jpg_folder)

