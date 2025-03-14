#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug 22 16:27:44 2023

@author: enoch
"""
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from glob import glob
from PIL import Image, ExifTags
from datetime import datetime
import os
import math
import pandas as pd


def get_timestamp_from_photo(image):
    try:
        exif_data = image._getexif()
        if exif_data is not None:
            for tag, value in exif_data.items():
                tag_name = ExifTags.TAGS.get(tag, tag)
                if tag_name == 'DateTimeOriginal':
                    return value
    except Exception as e:
        print("Error:", e)
    return None


def rename_photos_and_create_csv(path_to_photos, path_to_csv, well_abbr):
    photos1 = [f for f in sorted(glob(path_to_photos)) if not f.endswith('.db')]
    photo_df = pd.DataFrame(columns=["PHOTO NO.", "PHOTOGRAPHER", "DATE", "TIME", "LOCATION WHERE PHOTOGRAPH WAS TAKEN", "COMMENTS (I.E. DESCRIPTION OF WORK AND PURPOSE OF PHOTOGRAPH)"])
    datelist = []
    
    for photo in photos1:
        ext = photo.split('.')[-1]
        image = Image.open(photo)
        
        datetime_str = get_timestamp_from_photo(image)

        filepath = image.filename
        
        image.close()
    
        if isinstance(datetime_str, bytes):
              datetime_str = datetime_str.decode()
    
        counter = 1
    
        if datetime_str != None:
    
            datetime_obj = datetime.strptime(datetime_str, '%Y:%m:%d %H:%M:%S')
            date_form = datetime_obj.strftime("%Y/%m/%d")
            time_form = datetime_obj.strftime("%I:%M %p")
    
            for date in datelist:
                if date_form == date:
                    counter += 1
    
            datelist.append(date_form)
        else:
            date_form = None
            time_form = None
    
        if counter == 0:
            counter = ""
        elif counter < 10:
            counter = '_0' + str(counter)
        else:
            counter = '_' + str(counter)
    
        if date_form != None:
            new_name = f'{well_abbr}_{datetime_obj.strftime("%Y%m%d")}{counter}'
            loc_path = os.path.dirname(filepath)
            os.rename(filepath, os.path.join(loc_path, new_name + '.' + ext.lower()))
        else:
            new_name = os.path.basename(filepath)
    
        new_row = pd.DataFrame({
            "PHOTO NO.": [new_name],
            "PHOTOGRAPHER": [""],
            "DATE": ["" if date_form == None else date_form],
            "TIME": ["" if time_form == None else time_form],
            "LOCATION WHERE PHOTOGRAPH WAS TAKEN": [""],
            "COMMENTS (I.E. DESCRIPTION OF WORK AND PURPOSE OF PHOTOGRAPH)": [""]
        })
        
        photo_df = pd.concat([photo_df, new_row], ignore_index=True)
    
    photo_df.to_csv(path_to_csv)


def get_image_size(image_path):
    try:
        with Image.open(image_path) as img:
            width = img.width
            height = img.height
            return width, height
    except Exception as e:
        print("Error:", e)
        return None
    

def create_photo_pdf(photo_folder, output_pdf, well_name, proj_num, date, icon_path):
    pdf = canvas.Canvas(output_pdf, pagesize=letter)
    width, height = letter
    margin = inch / 2
    header = 1.75 * inch
    img_footer = 0.25 * inch
    img_cell_height = (height - header - margin - 2 * img_footer) / 2
    img_cell_width = width / 2 - margin
    
    #photos = glob(photo_folder)
    photos = [f for f in glob(photo_folder) if not f.endswith('.db')]
    
    for count in range(0, len(photos), 4):
        for count2 in range(4):
            if count + count2 < len(photos):
                try:
                    image=Image.open(photos[count + count2])

                    for orientation in ExifTags.TAGS.keys():
                        if ExifTags.TAGS[orientation]=='Orientation':
                            break
                    
                    exif = image._getexif()
                    
                    if not exif is None:

                        if exif[orientation] == 3:
                            image=image.rotate(180, expand=True)
                        elif exif[orientation] == 6:
                            image=image.rotate(270, expand=True)
                        elif exif[orientation] == 8:
                            image=image.rotate(90, expand=True)
                
                    image.save(photos[count + count2])
                    image.close()
                except (AttributeError, KeyError, IndexError):
                    # cases: image don't have getexif
                    pass
                
                org_img_width, org_img_height = get_image_size(photos[count + count2])
                
                if org_img_width >= org_img_height:
                    img_width = img_cell_width
                    img_height = org_img_height * img_width / org_img_height
                else:
                    img_height = img_cell_height
                    img_width = org_img_width * img_height / org_img_height
                if count2 == 0:
                    x_org = margin
                    y_org = margin + (height - margin - header) / 2 + img_footer
                elif count2 == 1:
                    x_org = width / 2
                    y_org = margin + (height - margin - header) / 2 + img_footer
                elif count2 == 2:
                    x_org = margin
                    y_org = margin
                else:
                    x_org = width / 2
                    y_org = margin
    
                
                photo_name = os.path.basename(photos[count + count2]).split('.')[0]
                text_width = pdf.stringWidth(photo_name, 'Helvetica', 12)
                pdf.setFont('Helvetica', 12)
    
                btm_lft_x_coord = x_org + ((width - 2 * margin) / 2 - img_width) / 2
                btm_lft_y_coord = y_org + (img_cell_height - img_height) / 2 + img_footer
                pdf.drawImage(photos[count + count2], btm_lft_x_coord, btm_lft_y_coord, width=img_width, height=img_height)
                pdf.drawString(x_org + (img_cell_width - text_width) / 2, y_org - 0.25 * img_footer, photo_name)
            
        
        pdf.drawString(x_org + (img_cell_width - text_width) / 2, y_org - 0.25 * img_footer, photo_name)
        pdf.drawString(x_org + (img_cell_width - text_width) / 2, y_org - 0.25 * img_footer, photo_name)
        
        pdf.setFont('Helvetica', 12)
        text_width = pdf.stringWidth(well_name, 'Helvetica', 12)
        pdf.drawString(width - margin - text_width, height - margin - 25, well_name)
        
        text_width = pdf.stringWidth(proj_num, 'Helvetica', 12)
        pdf.drawString(width - margin - text_width, height - margin - 40, proj_num)
        
        text_width = pdf.stringWidth(f'Page {str(int(count / 4) + 1)} of {str(math.floor(len(photos) / 4))}', 'Helvetica', 12)
        pdf.drawString(width - margin - text_width, height - margin - 55, f'Page {str(int(count / 4) + 1)} of {str(math.floor(len(photos) / 4) + 1)}')

        # pdf.drawString(margin, height - margin - 35, date)
        pdf.setLineWidth(3)
        pdf.setStrokeColorRGB(0.51, 0.02, 0.02)
        pdf.line(margin, height - margin - 65, width - margin, height - margin - 65)
        
        pdf.setFont('Helvetica-Oblique', 25)
        text_width = pdf.stringWidth('PHOTOGRAPHIC LOG', 'Helvetica-Oblique', 25)
        pdf.drawString(width - margin - text_width, height - margin - 10, 'PHOTOGRAPHIC LOG')
        
        icon_width, icon_height = get_image_size(icon_path)
        pdf.drawImage(icon_path, margin, height - margin - 55, width=icon_width * 75 / icon_height, height=75)
        
        pdf.showPage()
    pdf.save()



well_name = '13400 SOUTH AND BANGERTER SEWER RELOCATION'
well_abbr = '13400S'
proj_num = '419.23.200'
date = '02/26/2025'

icon_path = '.\\New Logo_Blue_90.tif'
photo_folder = '.\\Photos\\*'
output_pdf = '.\\photoLog.pdf'
output_csv = '.\\photoLog.csv'

rename_photos_and_create_csv(photo_folder, output_csv, well_abbr)
create_photo_pdf(photo_folder, output_pdf, well_name, proj_num, date, icon_path)
print(f"PDF created: {output_pdf}")

