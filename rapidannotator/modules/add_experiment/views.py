from flask import json, render_template, flash, redirect, url_for, request, jsonify, \
    current_app, g, abort, jsonify, session, send_file
from flask_babelex import lazy_gettext as _
from sqlalchemy.sql.functions import user
from werkzeug.utils import secure_filename

from rapidannotator import db
from rapidannotator.models import User, Experiment, AnnotatorAssociation, AnnotationCommentInfo, \
    DisplayTime, AnnotationLevel, Label, File, AnnotationInfo, FileCaption, AnnotationCaptionInfo, Clustering
from rapidannotator.modules.add_experiment import blueprint
from rapidannotator.modules.add_experiment.forms import AnnotationLevelForm, AnnotationTierForm
from rapidannotator.modules.elan.views import viewResults as elanViewResults
from rapidannotator import bcrypt
from math import ceil

from flask_login import current_user, login_required
from flask_login import login_user, logout_user, current_user
from .api import isPerimitted, isPerimitted1, isPerimitted2
from flask_paginate import Pagination, get_page_args
from sqlalchemy import and_
import os, csv, re

import xlwt, xlrd, pandas, datetime, random
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from io import BytesIO
import base64

import pandas as pd
from rapidannotator import app
import shutil

@blueprint.before_request
def before_request():
    if current_app.login_manager._login_disabled:
        pass
    elif not current_user.is_authenticated:
        return "Please login to access this page."
    elif not current_user.is_experimenter():
        return "You are not an experimenter, hence allowed to access this page."


@blueprint.route('/a/<int:experimentId>')
@isPerimitted1
def index(experimentId):
    users = User.query.all()
    experiment = Experiment.query.filter_by(id=experimentId).first()
    page, per_page, offset = get_page_args(page_parameter='page', per_page_parameter='per_page')
    expFiles = File.query.filter_by(experiment_id=experiment.id).limit(per_page).offset(offset)
    exp_files = []
    for fl in expFiles:
        fileCaption = FileCaption.query.filter_by(file_id=fl.id).first()
        exp_files.append((fl, fileCaption))
    total = ceil(File.query.filter_by(experiment_id=experiment.id).count())

    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap3')

    
    owners = experiment.owners
    annotators = experiment.annotators
    '''
        ..  there is no need to send the all the details
            of annotator like start / end / current file
            for annotation. So just send username of the
            annotator.
    '''
    annotators = [assoc.annotator for assoc in annotators]

    notOwners = [x for x in users if x not in owners]
    notAnnotators = [x for x in users if x not in annotators]

    if len(annotators) == 0:
        firstID = None
    else:
        firstID = annotators[0].id
        
    return render_template('add_experiment/main.html',
        users = users,
        experiment = experiment,
        notOwners = notOwners,
        notAnnotators = notAnnotators,
        exp_files=exp_files, page=page,
        per_page=per_page, pagination=pagination,
        firstID=firstID,
    )

@blueprint.route('/_addDisplayTimeDetails', methods=['GET','POST'])
def _addDisplayTimeDetails():   

    beforeTime = request.args.get('beforeTime', None)
    afterTime = request.args.get('afterTime', None)
    wordTime = request.args.get('wordTime', None)
    experimentId = request.args.get('experimentId', None)

    experiment = Experiment.query.filter_by(id=experimentId).first()
    experiment.display_time = DisplayTime(
        before_time = beforeTime,
        after_time = afterTime,
        num_words = wordTime,
    )
    db.session.commit()
    response = {}
    response['success'] = True

    return jsonify(response)


@blueprint.route('/_addOwner', methods=['GET','POST'])
def _addOwner():

    username = request.args.get('userName', None)
    experimentId = request.args.get('experimentId', None)

    experiment = Experiment.query.filter_by(id=experimentId).first()
    user = User.query.filter_by(username=username).first()
    experiment.owners.append(user)
    db.session.commit()
    response = {
        'success' : True,
        'ownerId' : user.id,
        'ownerUsername' : user.username,
    }

    return jsonify(response)


@blueprint.route('/_addLevelsShareUser', methods=['GET','POST'])
def _addLevelsShareUser():
    username = request.args.get('userName', None)
    experimentId = request.args.get('experimentId', None)
    experiment = Experiment.query.filter_by(id=experimentId).first()
    user = User.query.filter_by(username=username).first()
    experiment.sharing_levels_users.append(user)
    db.session.commit()
    response = {
        'success' : True,
        'userId' : user.id,
        'username' : user.username,
    }
    return jsonify(response)

def _annotatorsPlot(experiment):
    """Description:
        Utility function to making the annotators progress plot
    It is to be used inside adding/removing annotators functions 
    in order to update them on the settings page
    
    Args:
        experiment: Experiment model object
    Returns:
        pngImageB64String: encoded matplotlib plot image
    """
    annotatorDetails = experiment.annotators
    filesLength = experiment.files.count()
    bars = []
    xpos = []
    names, labels = [], []
    pngImageB64String = ""
    plt.clf()
    if filesLength > 0:
        for i, association in enumerate(annotatorDetails):
            user = User.query.filter_by(id=association.user_id).first()
            bars.append(((association.current*100)/filesLength))
            xpos.append(i+1)
            names.append(user.username)
            labels.append(str(association.current) + "/" + str(filesLength))

        if len(bars) > 0:
            barWidth = 0.35
            plt.bar(xpos, bars, width=barWidth, label='Annotator Progress')
            plt.legend()
            plt.xticks(xpos, names, rotation=90)
            for i in range(len(labels)):
                plt.text(x = xpos[i] - 0.1, y = bars[i] + 0.1, s = labels[i], size = 10)
            plt.subplots_adjust(bottom= 0.5, top = 0.98)
            plt.xlabel("Annotator's Name")
            plt.ylabel('Progress in Percentage')
            plt.ylim(0, 100)
            tmpfile = BytesIO()
            plt.savefig(tmpfile, format='png')
            pngImageB64String = "data:image/png;base64,"
            encoded = base64.b64encode(tmpfile.getvalue()).decode('utf-8')
            pngImageB64String = pngImageB64String + encoded
            plt.close()
        else:
            pngImageB64String = ""
    return pngImageB64String

@blueprint.route('/_addAnnotator', methods=['GET','POST'])
def _addAnnotator():

    username = request.args.get('userName', None)
    experimentId = request.args.get('experimentId', None)

    experiment = Experiment.query.filter_by(id=experimentId).first()
    user = User.query.filter_by(username=username).first()
    alreadyExists = AnnotatorAssociation.query.filter_by(user_id = user.id, experiment_id=experimentId).first()
    if (alreadyExists != None):
        response = {
            'success' : False,
            'annotatorId' : user.id,
            'annotatorUsername' : user.username,
            'message' : "User already exists in this experiment"
        }
        return jsonify(response)

    experimentAnnotator = AnnotatorAssociation()
    experimentAnnotator.experiment = experiment
    experimentAnnotator.annotator = user
    db.session.add(experimentAnnotator)
    db.session.commit()
    annotatorsPlot = _annotatorsPlot(experiment)
    response = {
        'success' : True,
        'annotatorId' : user.id,
        'annotatorUsername' : user.username,
        'plot': annotatorsPlot,
    }

    return jsonify(response)

@blueprint.route('/_displayTargetCaption', methods=['GET', 'POST'])
def _displayTargetCaption():
    optionVal = request.args.get('optionVal', None)
    experimentId = request.args.get('experimentId', None)

    experiment = Experiment.query.filter_by(id=experimentId).first()
    if optionVal == "Yes":
        experiment.displayTargetCaption = 1
    else:
        experiment.displayTargetCaption = 0
    db.session.commit()

    response = {
        'success' : True,
    }
    return jsonify(response)

@blueprint.route('/labels/<int:experimentId>')
@isPerimitted1
def editLabels(experimentId):

    experiment = Experiment.query.filter_by(id=experimentId).first()
    annotation_levels = AnnotationLevel.query.filter_by(experiment_id=\
                        experimentId).order_by(AnnotationLevel.level_number)
    annotationLevelForm = None
    if experiment.category != 'elan': 
        annotationLevelForm = AnnotationLevelForm(experimentId = experimentId)
    else:
        annotationLevelForm = AnnotationTierForm(experimentId = experimentId)
    annotationInfo = AnnotatorAssociation.query.filter_by(experiment_id=experimentId, user_id=current_user.id)
    if annotationInfo.count() > 0:
        annotationInfo = AnnotatorAssociation.query.filter_by(experiment_id=experimentId, user_id=current_user.id).first()
        annotationCount = annotationInfo.current
    else:
        annotationCount = 0
    skipLevel = {}
    for level in annotation_levels:
        label = Label.query.filter_by(annotation_id=level.id, skip=1)
        if label.count() > 0:
            skipLevel[level.id] = 1
        else:
            skipLevel[level.id] = 0

    return render_template('add_experiment/labels.html',
        experiment = experiment,
        annotation_levels = annotation_levels,
        annotationLevelForm = annotationLevelForm,
        annotationCount = annotationCount,
        is_global = (experiment.is_global)*1,
        skipLevel = skipLevel,
    )

@blueprint.route('/_addAnnotationLevel', methods=['POST'])
def _addAnnotationLevel():

    annotationLevelForm = AnnotationLevelForm()

    experimentId = annotationLevelForm.experimentId.data
    experiment = Experiment.query.filter_by(id=experimentId).first()
    annotation_levels = experiment.annotation_levels
    if experiment.category == 'elan': 
        annotationLevelForm = AnnotationTierForm()

    if annotationLevelForm.validate_on_submit():
        levelNumberValidated = True

        levelNumber = annotationLevelForm.levelNumber.data
        if levelNumber:
            existing = AnnotationLevel.query.filter(and_\
                        (AnnotationLevel.experiment_id==experimentId, \
                        AnnotationLevel.level_number==levelNumber)).first()
            if existing is not None:
                annotationLevelForm.levelNumber.errors.append('Level Number already used')
                levelNumberValidated = False

        if levelNumberValidated:
            annotationLevel = AnnotationLevel(
                name = annotationLevelForm.name.data,
                description = annotationLevelForm.description.data,
                instruction = annotationLevelForm.instruction.data,
                multichoice = annotationLevelForm.multichoice.data,
                labels_others = annotationLevelForm.labels_others.data,
            )
            if levelNumber:
                annotationLevel.level_number = annotationLevelForm.levelNumber.data
            experiment.annotation_levels.append(annotationLevel)
            db.session.commit()
            return redirect(url_for('add_experiment.editLabels', experimentId = experimentId))

    errors = "annotationLevelErrors"
    annotationInfo = AnnotatorAssociation.query.filter_by(experiment_id=experimentId, user_id=current_user.id)
    if annotationInfo.count() > 0:
        annotationInfo = AnnotatorAssociation.query.filter_by(experiment_id=experimentId, user_id=current_user.id).first()
        annotationCount = annotationInfo.current
    else:
        annotationCount = 0

    skipLevel = {}
    for level in annotation_levels:
        label = Label.query.filter_by(annotation_id=level.id, skip=1)
        if label.count() > 0:
            skipLevel[level.id] = 1
        else:
            skipLevel[level.id] = 0

    return render_template('add_experiment/labels.html',
        experiment = experiment,
        annotation_levels = annotation_levels,
        annotationLevelForm = annotationLevelForm,
        annotationCount = annotationCount,
        is_global = (experiment.is_global)*1,
        skipLevel = skipLevel,
        errors = errors,
    )
@blueprint.route('/_reorderAnnotationLevels', methods=['POST'])
def _reorderAnnotationLevels():
    """ Reordering the annotation levels
    Request Args:
        data: dict. contains each annoationlevel as key and its new order as value
        experimentId: the experiment id to be edited
    Returns:
        response: {success:True} when it is has updated 
        and {success:False} when something is missed from the expected inputs.
    """
    data = request.get_json(force=True)
    order = data.get('order')
    experimentId = data.get('experimentId')
    experiment = Experiment.query.filter_by(id=experimentId).first()
    # Checks whether experimentId is okay and ordering info is compelete or not
    if not experiment or not isinstance(order,dict):
        response = {'success' : False, 'message': "Incorrect request parameters"}
        return jsonify(response)
    # Making sure each level is given with its new order
    for level in experiment.annotation_levels:
        if order.get(str(level.id),None) == None:
            response = {'success' : False, 'message': "Incompelete annotation levels info"}
            return jsonify(response)
    # Updating each level with its new order
    for level in experiment.annotation_levels:
        level.level_number = order[str(level.id)]
    db.session.commit()
    response = {
        'success' : True,
    }
    return jsonify(response)

@blueprint.route('/_addLabels', methods=['POST','GET'])
def _addLabels():

    annotationId = request.args.get('annotationId', None)
    labelName = request.args.get('labelName', None)
    labelKey = request.args.get('labelKey', None)
    skipValue = request.args.get('skipValue', None)

    if labelKey == ' ':
        response = {
            'error' : 'Invalid Key',
        }
        return jsonify(response)

    annotationLevel = AnnotationLevel.query.filter_by(id=annotationId).first()
    annotationLabels = Label.query.filter_by(annotation_id=annotationId).all()

    for label in annotationLabels:
        if labelName == label.name:
            response = {
                'error' : 'Name already taken',
            }
            return jsonify(response)
        if labelKey and (labelKey == label.key_binding):
            response = {
                'error' : 'Key already taken',
            }
            return jsonify(response)

    label = Label(
        name = labelName,
        key_binding = labelKey,
        skip = int(skipValue),
    )
    annotationLevel.labels.append(label)

    db.session.commit()
    labelId = label.id

    response = {
        'success' : True,
        'labelId' : labelId,
    }

    return jsonify(response)

@blueprint.route('/_deleteLabel', methods=['POST','GET'])
def _deleteLabel():

    labelId = request.args.get('labelId', None)
    Label.query.filter_by(id=labelId).delete()

    db.session.commit()
    response = {}
    response['success'] = True

    return jsonify(response)

@blueprint.route('/_deleteAnnotationLevel', methods=['POST','GET'])
def _deleteAnnotationLevel():

    annotationId = request.args.get('annotationId', None)
    AnnotationLevel.query.filter_by(id=annotationId).delete()

    db.session.commit()
    response = {}
    response['success'] = True

    return jsonify(response)

@blueprint.route('/_editAnnotationLevel', methods=['POST','GET'])
def _editAnnotationLevel():

    annotationId = request.args.get('annotationId', None)
    annotationLevel = AnnotationLevel.query.filter_by(id=annotationId).first()

    annotationLevel.name = request.args.get('annotationName', None)
    annotationLevel.description = request.args.get('annotationDescription', None)
    annotationLevel.level_number = request.args.get('annotationLevelNumber', None)
    annotationLevel.instruction = request.args.get('annotationLevelInstruction', None)
    annotationLevel.multichoice = request.args.get('multichoice', None) == 'true'
    annotationLevel.labels_others = request.args.get('labels_others', None) == 'true'

    db.session.commit()
    response = {}
    response['success'] = True

    return jsonify(response)

@blueprint.route('/_editLabel', methods=['POST','GET'])
def _editLabel():

    labelId = request.args.get('labelId', None)
    skipValue = request.args.get('skipValue', None)
    experimentId = request.args.get('experimentId', None)

    label = Label.query.filter_by(id=labelId).first()

    label.name = request.args.get('labelName', None)
    label.key_binding = request.args.get('labelKey', None)
    label.skip = int(skipValue)

    db.session.commit()

    response = {}

    skipLevel = {}
    experiment = Experiment.query.filter_by(id=experimentId).first()
    annotation_levels = experiment.annotation_levels
    for level in annotation_levels:
        label = Label.query.filter_by(annotation_id=level.id, skip=1)
        if label.count() > 0:
            skipLevel[level.id] = 1
        else:
            skipLevel[level.id] = 0

    response['skipLevel'] = skipLevel
    response['success'] = True

    return jsonify(response)

@blueprint.route('/_togglePrivate', methods=['POST','GET'])
def _togglePrivate():

    experimentId = request.args.get('experimentId', None)
    experiment = Experiment.query.filter_by(id=experimentId).first()
    
    experiment.is_global = not experiment.is_global
    db.session.commit()

    response = {}
    response['success'] = True

    return jsonify(response)

@blueprint.route('/_addGlobalName', methods=['POST', 'GET'])
def _addGlobalName():
    
    globalName = request.args.get('globalName', None)
    experimentId = request.args.get('experimentId', None)
    experiment = Experiment.query.filter_by(id=experimentId).first()
    experiment.globalName = globalName
    db.session.commit()

    response = {}
    response['success'] = True

    return jsonify(response)

@blueprint.route('/_importAnnotationtLevel/<int:experimentId>')
@isPerimitted1
def _importAnnotationtLevel(experimentId):

    import_experiment = Experiment.query.filter_by(id=experimentId).first()

    global_annotation_level = []
    owners = []
    import_id = []
    global_names = []
    experiment_disp = []
    myExperiments = Experiment.query.all()

    for experiment in myExperiments:
        if experiment.id == experimentId:
            continue
        experiment_owners = experiment.owners.all()
        experiment_sharing_levels_users = experiment.sharing_levels_users.all()
        current_user_is_owner = [owner for owner in experiment_owners if owner.id == current_user.id]
        current_user_sharing = [sharing for sharing in experiment_sharing_levels_users if sharing.id == current_user.id]
        if experiment.is_global or current_user_is_owner or current_user_sharing:
            annotation_levels = experiment.annotation_levels
            global_annotation_level.append(annotation_levels)
            owners.append(experiment_owners)
            experiment_disp.append(experiment)
            import_id.append(experiment.id)
            global_names.append(experiment.globalName)

    return render_template('add_experiment/import.html',
        global_annotation_level = global_annotation_level,
        import_experiment = import_experiment,
        owners = owners,
        import_id = import_id,
        global_names = global_names,
        experiment_disp = experiment_disp,
    )

@blueprint.route('/_addImportedLevels', methods=['POST','GET'])
def _addImportedLevels():
    
    exportExperimentId = request.args.get('exportExperimentId', None)
    importExperimentId = request.args.get('importExperimentId', None)
    experiment = Experiment.query.filter_by(id=importExperimentId).first()

    annotation_levels = AnnotationLevel.query.filter_by(experiment_id=exportExperimentId).all()
    msg_already_imported = 1

    for level in annotation_levels:
        labels = Label.query.filter_by(annotation_id=level.id).all()
        cnt = AnnotationLevel.query.filter_by(experiment_id=importExperimentId, level_number=level.level_number).count()
        if cnt > 0:
            continue
        new_annotation_level = AnnotationLevel(experiment_id=importExperimentId, name=level.name, \
            description=level.description, level_number=level.level_number)
        experiment.annotation_levels.append(new_annotation_level)
        db.session.commit()

        new_annotation_level_id = AnnotationLevel.query.order_by(AnnotationLevel.id.desc()).first().id
        
        for label in labels:
            new_label = Label(annotation_id=new_annotation_level_id, name=label.name, key_binding=label.key_binding, skip=label.skip)
            new_annotation_level.labels.append(new_label)
            db.session.commit()
        
        msg_already_imported = 0

    response = {}
    response['success'] = True
    response['msg_already_imported'] = msg_already_imported

    return jsonify(response)

@blueprint.route('/skipLevels', methods=['POST', 'GET'])
def skipLevels():
    
    annotationId = request.args.get('annotationId', None)
    experimentId = request.args.get('experimentId', None)
    levels = AnnotationLevel.query.filter_by(experiment_id=\
                experimentId).order_by(AnnotationLevel.id)
    for level in levels:
        if int(level.id) > int(annotationId):
            level.skip = 1
        else:
            level.skip  = 0
        db.session.commit()

    response = {}
    response['success'] = True

    return jsonify(response)

@blueprint.route('/_uploadFiles', methods=['POST','GET'])
def _uploadFiles():

    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part')
            return redirect(request.url)
        flaskFile = request.files['file']
        if flaskFile.filename == '':
            flash('No selected file')
            return response
        ''' TODO? also check for the allowed filename '''
        if flaskFile:
            fl_name, fl_ext = os.path.splitext(flaskFile.filename)
            experimentId = request.form.get('experimentId', None)
            experiment = Experiment.query.filter_by(id=experimentId).first()
            if experiment.is_done:
                experiment.is_done = not experiment.is_done
                experiment.status = 'In Progress'
                db.session.commit()

            if experiment.uploadType == 'viaSpreadsheet':
                if fl_ext == '.xls':
                    addFilesViaSpreadsheetXLS(experimentId, flaskFile)
                elif fl_ext == '.csv':
                    addFilesViaSpreadsheetCSV(experimentId, flaskFile)
                else:
                    flash('Currently, rapidannotator does not support the selected file format')
                    return redirect(request.url)
            elif experiment.uploadType == 'fromConcordance':
                addFilesFromConcordance(experimentId, flaskFile)
            else:
                filename = secure_filename(request.form.get('fileName', None))
                newFile = File(name=filename)
                experiment.files.append(newFile)
                fileCaption = request.form.get('fileCaption', None)

                if experiment.category == 'text':
                    flaskFile.seek(0)
                    fileContents = flaskFile.read()
                    newFile.content = fileContents
                else:
                    '''
                        check if the directory for this experiment
                        ..  already exists
                        ..  if not then create
                    '''
                    experimentDir = os.path.join(app.config['UPLOAD_FOLDER'],
                                            str(experimentId))
                    if not os.path.exists(experimentDir):
                        os.makedirs(experimentDir)

                    file_name, file_extension = os.path.splitext(filename)
                    obj = File.query.order_by(File.id.desc()).first()
                    updatedName = secure_filename(file_name + '_' + str(obj.id) + file_extension)
                    newFile.content = updatedName
                    filePath = os.path.join(experimentDir, updatedName)
                    flaskFile.save(filePath)

                db.session.add(newFile)
                db.session.commit()

                newFileCaption = FileCaption(caption=fileCaption, target_caption=fileCaption, file_id = newFile.id)
                db.session.add(newFileCaption)
                db.session.commit()

                response = {
                    'success' : True,
                    'fileId' : newFile.id,
                }

                return jsonify(response)

    response = "success"

    return jsonify(response)

def addFilesViaSpreadsheetXLS(experimentId, spreadsheet):
    experiment = Experiment.query.filter_by(id=experimentId).first()

    filename = 'temp_' + current_user.username + '.xls'
    filePath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    spreadsheet.save(filePath)

    book = xlrd.open_workbook(filePath)
    first_sheet = book.sheet_by_index(0)
    for i in range(first_sheet.nrows):
        name = str(first_sheet.cell(i, 0).value)
        caption = str(first_sheet.cell(i, 2).value)
        content = str(first_sheet.cell(i, 1).value)
        newFile = File(name=name[:1024],
                    content=content[:32000],
                    experiment_id=experimentId,
        )
        db.session.add(newFile)
        db.session.commit()
        newFileCaption = FileCaption(caption=caption[:16000],
                    target_caption=caption[:16000],
                    file_id=newFile.id,
        )
        db.session.add(newFileCaption)
        db.session.commit()
        experiment.files.append(newFile)
    db.session.commit()
    os.remove(filePath)

def addFilesViaSpreadsheetCSV(experimentId, spreadsheet):
    experiment = Experiment.query.filter_by(id=experimentId).first()

    filename = 'temp_' + current_user.username + '.csv'
    filePath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    spreadsheet.save(filePath)

    csv_data = pandas.read_csv(filePath, names= ['file_name', 'content', 'caption'], delimiter=',')
    for i, row in csv_data.iterrows():
        name, content, caption = str(row['file_name']), row['content'], row['caption']
        newFile = File(name=name[:1024],
                    content=content[:32000],
                    experiment_id=experimentId,
        )
        db.session.add(newFile)
        db.session.commit()
        newFileCaption = FileCaption(caption=caption[:16000],
                    target_caption=caption[:16000],
                    file_id=newFile.id,
        )
        db.session.add(newFileCaption)
        db.session.commit()
        experiment.files.append(newFile)

    db.session.commit()
    os.remove(filePath)


def convert_txt_to_csv(inFileName, outFileName):
    with open(inFileName, encoding='utf-8') as tsvfile:
        in_txt = csv.reader(tsvfile, delimiter = '\t')
        out_csv = csv.writer(open(outFileName, 'w', encoding='utf-8'))
        out_csv.writerows(in_txt)


def addFilesFromConcordance(experimentId, concordance):
    experiment = Experiment.query.filter_by(id=experimentId).first()

    experimentDir = os.path.join(app.config['UPLOAD_FOLDER'], str(experimentId))
    if not os.path.exists(experimentDir):
        os.makedirs(experimentDir)
    filename = 'temp_' + current_user.username + '.txt'
    filePath = os.path.join(experimentDir, filename)
    outfilePath = os.path.join(experimentDir, "concordance.csv")
    concordance.save(filePath)
    convert_txt_to_csv(filePath, outfilePath)
    experiment = Experiment.query.filter_by(id=experimentId).first()
    concordance_lineNum = 1

    with open(filePath, encoding='utf-8') as tsvfile:
        reader = csv.DictReader(tsvfile, dialect='excel-tab', quoting=csv.QUOTE_NONE)
        if "Structure ``text_file''" in reader.fieldnames:
            readnamefromattributetext_file = True  # NewScape style
        else:
            readnamefromattributetext_file = False # YouTube style
        for row in reader:
            caption = row["Context before"] + " " + row["Query item"] +  " " + row["Context after"]
            target_caption = row["Query item"]
            if experiment.category == "video" or experiment.category == "elan":
                content = row["Video Snippet"]
            elif experiment.category == "audio":
                content = row["Audio Snippet"]
            elif experiment.category == "image":
                content = row["Screenshot"]
            else:
                content = caption
            # Only make the edge_link = row["Video Url"] if it is a new scape style
            edge_link = ""
            if readnamefromattributetext_file:
                edge_link = row["Video URL"]
                name_temp = (row["Structure ``text_file''"]).replace(".txt", "")
                name_match = re.search("([0-9]{4}-[0-9]{2}-[0-9]{2}_.*)$", name_temp)
                name = name_match.group(1)
            else:
                name = row["Text ID"]
            # Add a timestamp to the name:
            if experiment.category == "image":
                imageresults = re.search("start=(.*)$", row["Screenshot"])
                name = name + "__" + imageresults.group(1)
            else:
                timeresults = re.search("start=(.*)&end=(.*)$", row["Video Snippet"])
                name = name + "__" + timeresults.group(1) + "-" + timeresults.group(2)
            
            random.seed(datetime.datetime.now())
            display_order = random.randint(1, 506070800)
            
            newFile = File(name=name[:1024],
                    content=content[:32000],
                    experiment_id=experimentId,
                    concordance_lineNumber=concordance_lineNum,
                    display_order = display_order,
                    edge_link = edge_link,
            )
            db.session.add(newFile)
            db.session.commit()
            newFileCaption = FileCaption(caption=caption[:16000],
                    target_caption=target_caption[:16000],
                    file_id=newFile.id,
            )
            db.session.add(newFileCaption)
            db.session.commit()
            concordance_lineNum = concordance_lineNum + 1
            experiment.files.append(newFile)
    db.session.commit()
    os.remove(filePath)

@blueprint.route('/_deleteFile', methods=['POST','GET'])
def _deleteFile():

    ''' TODO? check when to import app '''

    experimentCategory = request.args.get('experimentCategory', None)
    experimentId = request.args.get('experimentId', None)
    experiment = Experiment.query.filter_by(id=experimentId).first()
    fileId = request.args.get('fileId', None)

    currFile = File.query.filter_by(id=fileId).first()
    currFileCaption = FileCaption.query.filter_by(file_id=fileId).first()

    if experiment.uploadType == 'manual' and experiment.category != 'text':
        '''
            check if the directory for this experiment
            ..  already exists
            ..  if not then create
        '''
        experimentDir = os.path.join(app.config['UPLOAD_FOLDER'],
                                str(experimentId))
        if not os.path.exists(experimentDir):
            response = {
                'error' : "specified experiment doesn't have any file",
            }
            return jsonify(response)
        
        filePath = os.path.join(experimentDir, currFile.content)
        os.remove(filePath)

    db.session.delete(currFile)
    db.session.delete(currFileCaption)
    db.session.commit()
    response = {}
    response['success'] = True
    return jsonify(response)


@blueprint.route('/_deleteAllFiles', methods=['POST','GET'])
def _deleteAllFiles():


    experimentId = request.args.get('experimentId', None)
    experiment = Experiment.query.filter_by(id=experimentId).first()

    allFiles = experiment.files

    for fl in allFiles:
        currFile = File.query.filter_by(id=fl.id).first()
        currFileCaption = FileCaption.query.filter_by(file_id=fl.id).first()
        if experiment.uploadType == 'manual' and experiment.category != 'text':
            experimentDir = os.path.join(app.config['UPLOAD_FOLDER'], str(experimentId))
            if not os.path.exists(experimentDir):
                response = {
                    'error' : "specified experiment doesn't have any file",
                }
                return jsonify(response)
            filePath = os.path.join(experimentDir, currFile.content)
            os.remove(filePath)
        db.session.delete(currFile)
        db.session.delete(currFileCaption)
        db.session.commit()
    
    response = {}
    response['success'] = True
    return jsonify(response)


@blueprint.route('/_updateFileName', methods=['POST','GET'])
def _updateFileName():


    fileId = request.args.get('fileId', None)
    currentFile = File.query.filter_by(id=fileId).first()
    experiment = Experiment.query.filter_by(id=currentFile.experiment_id).first()

    updatedName = secure_filename(request.args.get('name', currentFile.name))

    if experiment.uploadType == 'manual' and experiment.category != 'text':	
        experimentDir = os.path.join(app.config['UPLOAD_FOLDER'],	
                                    str(currentFile.experiment_id))	

        file_name, file_extension = os.path.splitext(updatedName)
        currentFilePath = os.path.join(experimentDir, currentFile.content)	
        currentFile.content = file_name + '_' + str(fileId) + file_extension
        newFilePath = os.path.join(experimentDir, currentFile.content)	
        os.rename(currentFilePath, newFilePath)

    currentFile.name = updatedName

    db.session.commit()
    response = {}
    response['success'] = True

    return jsonify(response)


@blueprint.route('/_updateFileCaption', methods=['POST','GET'])
def _updateFileCaption():

    fileId = request.args.get('fileId', None)
    currentFile = FileCaption.query.filter_by(file_id=fileId).first()

    currentFile.caption = request.args.get('caption', None)

    db.session.commit()
    response = {}
    response['success'] = True

    return jsonify(response)

@blueprint.route('/viewSettings/<int:experimentId>')
@isPerimitted1
def viewSettings(experimentId):

    users = User.query.all()
    experiment = Experiment.query.filter_by(id=experimentId).first()
    owners = experiment.owners
    levelsSharedUsers = experiment.sharing_levels_users
    ''' send all the details of each annotator. '''
    annotatorDetails = experiment.annotators
    annotators = [assoc.annotator for assoc in annotatorDetails]

    notOwners = [x for x in users if x not in owners]
    notAnnotators = [x for x in users if x not in annotators]
    notLevelsSharedUsers = [x for x in users if x not in levelsSharedUsers and x not in owners]

    totalFiles = experiment.files.count()

    filesLength = experiment.files.count()
    bars = []
    xpos = []
    names, labels = [], []
    displayImg = 0
    pngImageB64String = ""
    plt.clf()
    if filesLength > 0:
        for i, association in enumerate(annotatorDetails):
            user = User.query.filter_by(id=association.user_id).first()
            bars.append(((association.current*100)/filesLength))
            xpos.append(i+1)
            names.append(user.username)
            labels.append(str(association.current) + "/" + str(filesLength))

        if len(bars) > 0:
            displayImg = 1 
            barWidth = 0.35
            fig = plt.figure()
            plt.bar(xpos, bars, width=barWidth, label='Annotator Progress')
            plt.legend()

            plt.xticks(xpos, names, rotation=90)
            for i in range(len(labels)):
                plt.text(x = xpos[i] - 0.1, y = bars[i] + 0.1, s = labels[i], size = 10)
            plt.subplots_adjust(bottom= 0.5, top = 0.98)
            plt.xlabel("Annotator's Name")
            plt.ylabel('Progress in Percentage')
            plt.ylim(0, 100)
            tmpfile = BytesIO()
            plt.savefig(tmpfile, format='png')
            pngImageB64String = "data:image/png;base64,"
            encoded = base64.b64encode(tmpfile.getvalue()).decode('utf-8')
            pngImageB64String = pngImageB64String + encoded
            plt.close()
        else:
            displayImg = 0
            pngImageB64String = ""

    clustering =  Clustering.query.filter_by(experiment_id=experimentId, user_id=int(current_user.id)).first()
    if clustering is None:
        clustering_status = -1
        displayCluster = 0
    else:
        clustering_status = int(clustering.status)
        displayCluster = (clustering.display)*1

    return render_template('add_experiment/settings.html',
        users = users,
        experiment = experiment,
        owners = owners,
        levelsSharedUsers = levelsSharedUsers,
        notOwners = notOwners,
        notAnnotators = notAnnotators,
        annotatorDetails = annotatorDetails,
        notLevelsSharedUsers = notLevelsSharedUsers,
        totalFiles = totalFiles,
        displayImg = displayImg,
        html = pngImageB64String,
        current_user=current_user,
        clustering_status= clustering_status,
        displayCluster = displayCluster,
    )

@blueprint.route('/_deleteAnnotator', methods=['POST','GET'])
def _deleteAnnotator():

    annotatorId = request.args.get('annotatorId', None)
    experimentId = request.args.get('experimentId', None)

    experimentAnnotator = AnnotatorAssociation.query.filter_by(
                            experiment_id = experimentId,
                            user_id = annotatorId)
    experimentAnnotator.delete()
    experiment = Experiment.query.filter_by(id=experimentId).first()
    annotatorsPlot = _annotatorsPlot(experiment)
    db.session.commit()

    response = {'success': True, 'plot': annotatorsPlot}
    
    return jsonify(response)

@blueprint.route('/_editAnnotator', methods=['POST','GET'])
def _editAnnotator():

    annotatorId = request.args.get('annotatorId', None)
    experimentId = request.args.get('experimentId', None)
    annotatorDetails = AnnotatorAssociation.query.filter_by(
                        experiment_id=experimentId,
                        user_id=annotatorId).first()

    annotatorDetails.start = request.args.get('start', annotatorDetails.start)
    annotatorDetails.end = request.args.get('end', annotatorDetails.end)
    annotatorDetails.current = 0

    db.session.commit()
    response = {}
    response['success'] = True

    return jsonify(response)

@blueprint.route('/_equalDataParition', methods=['POST','GET'])
def _equalDataParition():

    annotators = request.args.get('annotatorsDict', None)
    annotators = annotators.split(',')
    experimentId = request.args.get('experimentId', None)
    experiment = Experiment.query.filter_by(id=experimentId).first()
    numAnnotators = len(annotators)
    numFiles = experiment.files.count()

    start, step = 0,0

    for annotator in annotators:
        annotatorId = User.query.filter_by(username=annotator).first().id
        annotatorDetails = AnnotatorAssociation.query.filter_by(experiment_id=experimentId,\
            user_id=annotatorId).first()
        step = numFiles // numAnnotators
        annotatorDetails.start = start
        annotatorDetails.end = start + step
        db.session.commit()
        start = start + step
        numFiles = numFiles - step
        numAnnotators = numAnnotators - 1
   
    response = {}
    response['success'] = True

    return jsonify(response)

@blueprint.route('/_deleteOwner', methods=['POST','GET'])
def _deleteOwner():

    ownerId = request.args.get('ownerId', None)
    experimentId = request.args.get('experimentId', None)
    user = User.query.filter_by(id=ownerId).first()
    experiment = Experiment.query.filter_by(id=experimentId).first()
    experiment.owners.remove(user)

    db.session.commit()

    response = {}
    response['success'] = True

    return jsonify(response)

@blueprint.route('/_deleteLevelsShareUser', methods=['POST','GET'])
def _deleteLevelsShareUser():
    userId = request.args.get('userId', None)
    experimentId = request.args.get('experimentId', None)
    user = User.query.filter_by(id=userId).first()
    experiment = Experiment.query.filter_by(id=experimentId).first()
    experiment.sharing_levels_users.remove(user)
    db.session.commit()
    response = {}
    response['success'] = True
    return jsonify(response)


@blueprint.route('/_deleteExperiment', methods=['POST','GET'])
def _deleteExperiment():

    experimentId = request.args.get('experimentId', None)
    experiment = Experiment.query.filter_by(id=experimentId).first()
    experiment.owners = []
    db.session.delete(experiment)
    db.session.commit()

    experimentDir = os.path.join(app.config['UPLOAD_FOLDER'],
                            str(experimentId))

    if os.path.exists(experimentDir):
        shutil.rmtree(experimentDir)


    response = {}
    response['success'] = True

    return jsonify(response)


@blueprint.route('/viewResults/<int:experimentId>', defaults={'userId': None})
@blueprint.route('/viewResults/<int:experimentId>/<int:userId>')
@isPerimitted2
def viewResults(experimentId, userId):
    """ Viewing results of a user/annotator's annotation at an experiment.
    Args:
        experimentId: Id of the experiment requested to be viewed.
        userId: (optional) Id of a specific annotator of that experiment.
        levelId: (optional) Id of annotation level, filtering option.
        labelId: (optional) Id of annotation level's label, filtering option.
    Returns: 
        HTML view @add_experiment/results.html
    """
    levelId = request.args.get('levelId', None)
    labelId = request.args.get('labelId', None)

    page, per_page, offset = get_page_args(page_parameter='page', per_page_parameter='per_page')
    experiment = Experiment.query.filter_by(id=experimentId).first()

    if (userId == None):
        return render_template('add_experiment/noResults.html', experiment = experiment)
    
    if experiment.category == 'elan':
        return elanViewResults(experimentId, userId, page, per_page, offset)
        
    expFiles = File.query.filter_by(experiment_id=experiment.id).limit(per_page).offset(offset)

    selected_level = None
    selected_label = None

    annotation_levels = experiment.annotation_levels
    # Finding the selected annotation level and setting it to selected_level.
    if levelId and levelId.isnumeric():
        for level in annotation_levels:
            if level.id == int(levelId):
                selected_level = level
                # Finding the selected label and setting it to selected_label.
                if labelId and labelId.isnumeric():
                    for label in level.labels:
                        if label.id == int(labelId):
                            selected_label = label
                            break
                break

    expFiles = []
    if not selected_level and not selected_label:
        expFiles = File.query.filter_by(experiment_id=experiment.id).limit(per_page).offset(offset)
    elif not selected_label:
        expFiles = File.query.filter_by(experiment_id=experiment.id)\
            .join(AnnotationInfo, AnnotationInfo.file_id == File.id)\
            .filter_by(annotationLevel_id = selected_level.id, user_id = userId)\
            .limit(per_page).offset(offset)
    else:
        expFiles = File.query.filter_by(experiment_id=experiment.id)\
            .join(AnnotationInfo, AnnotationInfo.file_id == File.id)\
            .filter_by(annotationLevel_id = selected_level.id, user_id = userId, label_id = selected_label.id)\
            .limit(per_page).offset(offset)

    annotators_assoc = experiment.annotators
    annotators = [assoc.annotator for assoc in annotators_assoc]

    
    total = ceil(File.query.filter_by(experiment_id=experiment.id).count())
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap3')

    annotations = {}
    user = User.query.filter_by(id=userId).first()
    for f in expFiles:
        annotation = {}
        fileAnnotations = AnnotationInfo.query.filter_by(file_id=f.id, user_id=userId)
        if fileAnnotations.count() == 0:
            annotations[f.id] = annotation
        else:
            for level in annotation_levels:
                annotation[level.id] = []
                anno_info = AnnotationInfo.query.filter_by(file_id=f.id, user_id=userId, annotationLevel_id=level.id).all()
                if anno_info is not None:
                    for label in level.labels:
                        info = AnnotationInfo.query.filter_by(file_id=f.id, user_id=userId, annotationLevel_id=level.id, label_id=label.id).first()
                        if info is not None:
                            annotation[level.id].append(label.name)
                else:
                    annotation[level.id] = "SKIPPED"
        annotations[f.id] = annotation       
    multichoice = AnnotationLevel.query.filter_by(experiment_id=experimentId, multichoice=True).count()
    return render_template('add_experiment/results.html', exp_files=expFiles, page=page, \
        per_page=per_page, pagination=pagination, experiment = experiment,\
        annotations = annotations, annotators=annotators, user=user, \
        annotation_levels=annotation_levels, multichoice=multichoice,\
        selected_level=selected_level, selected_label=selected_label)


@blueprint.route('/_discardAnnotations', methods=['POST','GET'])
def _discardAnnotations():

    experimentId = request.args.get('experimentId', None)
    annotationLevels = AnnotationLevel.query.filter_by(experiment_id=experimentId).all()
    experiment = Experiment.query.filter_by(id=experimentId).first()
    experiment.status = 'In Progress'
    experiment.is_done = 0

    '''
        ..  delete all the AnnotationInfo for all the levels
            of this experiment.
        ..  reset the current pointer to start pointer of the
            annotation.
    '''
    for level in annotationLevels:
        AnnotationInfo.query.filter_by(annotationLevel_id=level.id).delete()
        AnnotationCommentInfo.query.filter_by(annotationLevel_id=level.id).delete()

    annotatorsInfo = AnnotatorAssociation.query.filter_by(experiment_id=experimentId).all()
    for annotatorInfo in annotatorsInfo:
        annotatorInfo.current = 0

    commentInfo = AnnotationCommentInfo.query.filter_by(experiment_id=experimentId).all()
    for comm in commentInfo:
        comm.current = 0
    
    db.session.commit()
    response = {}
    response['success'] = True

    return jsonify(response)

@blueprint.route('/_discardSingleAnnotation', methods=['POST','GET'])
def _discardSingleAnnotation():
    
    experimentId = request.args.get('experimentId', None)
    fileId = request.args.get('fileId', None)

    experiment = Experiment.query.filter_by(id=experimentId).first()
    experiment.status = 'In Progress'
    annotationLevels = AnnotationLevel.query.filter_by(experiment_id=experimentId).all()

    '''
        ..  delete the AnnotationInfo for all the levels
            of this file.
        .. check if the file is annotated or not
        ..  decrease the current pointer of the
            annotation.
    '''
    is_annotated = 0
    
    for level in annotationLevels:
        is_annotated = AnnotationInfo.query.filter_by(user_id=current_user.id,\
            annotationLevel_id=level.id, file_id = fileId).delete()
    
    if is_annotated == 1:
        annotatorsInfo = AnnotatorAssociation.query.filter_by(experiment_id=experimentId).all()
        for annotatorInfo in annotatorsInfo:
            annotatorInfo.current = annotatorInfo.current - 1

    db.session.commit()
    response = {}
    response['success'] = True

    return jsonify(response)

@blueprint.route('/_showResultImages', methods=['POST','GET'])
def _showResultImages():
    
    experimentId = request.args.get('experimentId', None)
    experiment = Experiment.query.filter_by(id=experimentId).first()
    files = []
    for f in experiment.files:
        files.append((f.content, f.id))

    response = {}
    response['success'] = True
    response['files'] = files
    response['category'] = experiment.category
    response['uploadType'] = experiment.uploadType

    return jsonify(response)


@blueprint.route('/_exportResults/<int:experimentId>', methods=['POST','GET'])
def _exportResults(experimentId):

    # experimentId = request.args.get('experimentId', None)
    experiment = Experiment.query.filter_by(id=experimentId).first()


    excel_file = xlwt.Workbook()
    sheet = excel_file.add_sheet('results')
    sheet.col(0).width = 256 * 40
    row, col = 0, 0
    sheet.write(row, col, 'File Name')
    allLables, columnNumber = {}, {}

    annotationLevels = AnnotationLevel.query.filter_by(experiment_id=\
                        experimentId).order_by(AnnotationLevel.level_number)

    for level in annotationLevels:
        labels = Label.query.filter_by(annotation_id=level.id).order_by(Label.id)
        for label in labels:
            allLables[label.id] = 0
            col += 1
            columnNumber[label.id] = col
            sheet.write(row, col, label.name)
    col += 1
    sheet.write(row, col, 'Caption')
    if experiment.uploadType == 'viaSpreadsheet':
        col += 1
        sheet.write(row, col, 'Video Link')

    row, col = 0, 0
    for f in experiment.files:
        row += 1
        sheet.write(row, 0, f.name)
        annotation = {}
        for key in allLables:
            allLables[key] = 0

        fileAnnotations = AnnotationInfo.query.filter_by(file_id=f.id).all()
        for fileAnnotation in fileAnnotations:
            labelId = fileAnnotation.label_id
            allLables[labelId] += 1

        col = 1
        for key in allLables:
            sheet.write(row, columnNumber[key], allLables[key])
            col += 1
            allLables[key] = 0
    
        if f.caption == '':
            sheet.write(row, col, 'No Caption Provided')
        else:
            sheet.write(row, col, f.caption)
        if experiment.uploadType == 'viaSpreadsheet':
            col += 1
            sheet.write(row, col, f.content)

    filename = str(experimentId) + '.xls'

    filePath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    excel_file.save(filePath)

    response = {}
    response['success'] = True

    return send_file(filePath, as_attachment=True)

@blueprint.route('/_exportResultsXLS/<int:experimentId>', methods=['POST','GET'])
def _exportResultsXLS(experimentId):
    
    experiment = Experiment.query.filter_by(id=experimentId).first()
    excel_file = xlwt.Workbook()
    sheet = excel_file.add_sheet('results')
    style0 = xlwt.easyxf('font: name Arial, color-index black, bold on')
    sheet.col(0).width = 256 * 40
    row, col = 0, 0
    sheet.write(row, col, 'File Name', style0)
    RESERVED_LABEL = '99999'
    
    annotators_assoc = experiment.annotators
    annotators = [assoc.annotator for assoc in annotators_assoc]
    annotation_levels = AnnotationLevel.query.filter_by(experiment_id=experimentId).all()
    for annotator in annotators:
        col += 1
        for level in annotation_levels:
            sheet.write(row, col, annotator.username + " ( Level: " + str(level.name) + " )", style0)
            col += 1
        sheet.write(row, col, "Target Caption of " + annotator.username, style0)

    col += 1
    sheet.write(row, col, 'Caption', style0)
    if experiment.uploadType == 'viaSpreadsheet':
        col += 1
        sheet.write(row, col, 'Video Link', style0)

    row, col = 0, 0
    for f in experiment.files:
        row += 1
        sheet.write(row, 0, f.name)

        col = 0
        for annotator in annotators:
            col += 1
            for level in annotation_levels:
                annotation_info = AnnotationInfo.query.filter_by(file_id=f.id, user_id=annotator.id, annotationLevel_id=level.id).order_by(AnnotationInfo.annotationLevel_id)
                if annotation_info.count() == 0:
                    sheet.write(row, col, RESERVED_LABEL)
                else:
                    for info in annotation_info:
                        label = Label.query.filter_by(id=info.label_id).first()
                        sheet.write(row, col, str(label.name))
                col += 1
            cp = AnnotationCaptionInfo.query.filter_by(file_id=f.id, user_id=annotator.id).first()
            if cp == None:
                cp_val = FileCaption.query.filter_by(file_id=f.id).first()
                sheet.write(row, col, cp_val.target_caption)
            else:
                sheet.write(row, col, cp.target_caption)

        col += 1
        fileCaption = FileCaption.query.filter_by(file_id=f.id).first()
        if fileCaption.caption == '':
            sheet.write(row, col, 'No Caption Provided')
        else:
            sheet.write(row, col, fileCaption.caption)
        
        if experiment.uploadType == 'viaSpreadsheet':
            col += 1
            sheet.write(row, col, f.content)

    filename = str(experimentId) + '.xls'

    filePath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    excel_file.save(filePath)

    # when to remove file?
    # os.remove(filePath)

    response = {}
    response['success'] = True

    return send_file(filePath, as_attachment=True)


def _exportResultsConcordance(experiment, format1):

    experimentDIR = os.path.join(app.config['UPLOAD_FOLDER'], str(experiment.id))
    inputConcordance = os.path.join(experimentDIR, 'concordance.csv')
    data = pandas.read_csv(inputConcordance)
    
    RESERVED_LABEL = '99999'
    annotators_assoc = experiment.annotators
    annotators = [assoc.annotator for assoc in annotators_assoc]
    annotation_levels = AnnotationLevel.query.filter_by(experiment_id=experiment.id).all()
    col_num = len(data.axes[1])

    for annotator in annotators:
        for level in annotation_levels:
            data_headers = ["File Deleted" for itr in range(len(data))]
            data_coordinates = ["File Deleted" for itr in range(len(data))]
            for f in experiment.files:           
                annotation_info = AnnotationInfo.query.filter_by(file_id=f.id, user_id=annotator.id, annotationLevel_id=level.id).order_by(AnnotationInfo.annotationLevel_id)
                if annotation_info.count() == 0:
                    data_headers[f.concordance_lineNumber - 1] = RESERVED_LABEL
                    data_coordinates[f.concordance_lineNumber - 1] = ""
                else:
                    for info in annotation_info:
                        label = Label.query.filter_by(id=info.label_id).first()
                        data_headers[f.concordance_lineNumber - 1] = str(label.name)
                        data_coordinates[f.concordance_lineNumber - 1] = json.dumps(info.coordinates)
            data.insert(col_num, annotator.username + " ( Level: " + str(level.name) + " )" , data_headers)
            data.insert(col_num, annotator.username + " coordinates of ( Level: " + str(level.name) + " )" , data_coordinates)
            col_num += 1

        target_caption = ["No Caption Provided" for itr in range(len(data))]
        comments = ["No Comments" for itr in range(len(data))]
        for f in experiment.files: 
            cp = AnnotationCaptionInfo.query.filter_by(file_id=f.id, user_id=annotator.id).first()
            if cp == None:
                cp_val = FileCaption.query.filter_by(file_id=f.id).first()
                target_caption[f.concordance_lineNumber - 1] = cp_val.target_caption
            else:
                target_caption[f.concordance_lineNumber - 1] = cp.target_caption

            comments_info = AnnotationCommentInfo.query.filter_by(file_id=f.id, user_id=annotator.id).first()
            if comments_info == None:
                comment = "No Comments"
                comments[f.concordance_lineNumber - 1] = comment
            else:
                comments[f.concordance_lineNumber - 1] = comments_info.comment
        data.insert(col_num, "Target Caption of " + annotator.username, target_caption)
        col_num = col_num + 1
        data.insert(col_num, "Comments by " + annotator.username, comments)
        col_num = col_num + 1
    
    if experiment.display_time:
        before_time = experiment.display_time.before_time
        after_time = experiment.display_time.after_time
        data['Video Snippet Annotated'] = data['Video Snippet'].apply(lambda video_snippet: _addOffsetTime(video_snippet, before_time, after_time))
    else:
        data['Video Snippet Annotated'] = data['Video Snippet']
        
    if format1 == '.xlsx':
        filename = str(experiment.id) + '.xlsx'
        filePath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        with pandas.ExcelWriter(filePath, date_format='YYYY-MM-DD', datetime_format='YYYY-MM-DD HH:MM:SS') as writer:
            data.to_excel(writer, sheet_name='Sheet1', index=False)
    else:
        filename = str(experiment.id) + '.csv'
        filePath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        data.to_csv(filePath, index=False)

    response = {}
    response['success'] = True

    return send_file(filePath, as_attachment=True)

def _addOffsetTime(video_snippet, before_time, after_time):
    """ Utility function used at exporting concordance results.
    Modifying offset of the video snippet link
    by applying the following equations:
        new_start = video_snippet_start - exp.display_caption.before_time
        new_end = video_snippet_end + exp.display_caption.after_time
    and replacing those with the existing on the video snippet using regular expression
    Args:
        video_snippet: str. of the link expected to change it's offset
        before_time: float representes the exp.'s display_caption before_time
        after_time: float representes the exp.'s display_caption after_time
    Returns:
        video_snippet: modified video_snippet start and end times on the link
    """
    # If the sent video_snippet is not string, returning it without processing.
    if not isinstance(video_snippet, str):
        return video_snippet
    float_re = "([0-9]*)?[.]?([0-9]*)?"
    video_time = re.search(f'start=({float_re})&end=({float_re})$', video_snippet)
    # If the sent video_snippet is not matching the re. so it miss the start and end
    # Just returns it without any modifications
    if not video_time:
        return video_snippet
    start_time = float(video_time.group(1)) - before_time
    start_time = 0 if start_time < 0 else start_time
    end_time = float(video_time.group(2)) + after_time
    end_time = 0 if end_time < 0 else end_time
    video_snippet = re.sub(f'end=({float_re})', "end=" + str(end_time), video_snippet)
    video_snippet = re.sub(f'start=({float_re})', "start=" + str(start_time), video_snippet)
    return video_snippet

@blueprint.route('/changeDisplayOrder/<int:experimentId>/<string:displayType>', methods=['POST','GET'])
def changeDisplayOrder(experimentId, displayType):
    """
    Change the display order of the experiment files.
    Args:
        experimentId: int. The experiment id.
        order: str. The display order of the experiment files.
    Returns:
        response: dict. The response containing success and message keys.
    """

    experiment = Experiment.query.get(experimentId)
    if experiment == None:
        return jsonify({"success": False, "message": "Experiment does not exist"})

    if displayType not in ['fcfs', 'random']:
        return jsonify({"success": False, "message": "Invalid type"})

    experiment.displayType = displayType
    db.session.commit()

    return jsonify({"success": True, "message": "Order changed successfully"})

@blueprint.route('/changeDisplayTime/<int:experimentId>', methods=['POST'])

@blueprint.route('/_exportResultsCSV/<int:experimentId>/<string:format1>', methods=['POST','GET'])
def _exportResultsCSV(experimentId, format1):

    experiment = Experiment.query.filter_by(id=experimentId).first()
    if experiment.uploadType == 'fromConcordance':
        return _exportResultsConcordance(experiment, format1)
    
    RESERVED_LABEL = '99999'    
    
    levels_map = {}
    labels_map = {}
    annotators_map = {}

    annotators_assoc = experiment.annotators
    for assoc in annotators_assoc:
        annotators_map[assoc.annotator.id] = assoc.annotator.username
    
    levels = experiment.annotation_levels
    for level in levels:
        levels_map[level.id] = level.name
        for label in level.labels:
            labels_map[label.id] = label.name

    files_columns = [File.id, File.name, FileCaption.caption]

    if experiment.uploadType == 'viaSpreadsheet':
        files_columns.append(File.content)
    files = File.query.filter_by(experiment_id = experimentId)\
            .join(FileCaption, FileCaption.file_id == File.id)\
            .with_entities()\
            .add_columns(*files_columns)\
            .all()
    df = pd.DataFrame(files)
    if experiment.uploadType == 'viaSpreadsheet':
        df.columns = ['id', 'name', 'caption', 'content']
    else :
        df.columns = ['id', 'name', 'caption']
    df.set_index('id',inplace=True)
    RESERVED_COL = [RESERVED_LABEL] * len(files)
    EMPTY_COL = [""] * len(files)
    for annotator in annotators_map.values():
        for level in levels_map.values():
            df[annotator + " ( Level: " + level + " )"] = RESERVED_COL
            df[annotator + " coordinates of ( Level: " + level + " )"] = EMPTY_COL
        df['Target Caption of ' + annotator] = df['caption']
        df['Comments by ' + annotator] = ["No Comments"] * len(files)
    df.rename(columns={'name': 'Filename', 'caption':'Caption'}, inplace=True)
    
    if experiment.uploadType == 'viaSpreadsheet':
        df.rename(columns={'content': 'Video Link'}, inplace=True)

    annotations = AnnotationInfo.query.filter(AnnotationInfo.annotationLevel_id.in_(levels_map.keys()))
    for a in annotations:
        df.loc[a.file_id, [annotators_map[a.user_id] + " ( Level: " + levels_map[a.annotationLevel_id] + " )"]] = labels_map[a.label_id]
        if a.coordinates:
            df.loc[a.file_id, [annotators_map[a.user_id] + " coordinates of ( Level: " + levels_map[a.annotationLevel_id] + " )"]] = json.dumps(a.coordinates)
        
    
    captions_info = AnnotationCaptionInfo.query.filter(AnnotationCaptionInfo.file_id.in_(df.index.values))
    for ci in captions_info:
        df.loc[ci.file_id, ['Target Caption of ' + annotators_map[ci.user_id]]] = ci.target_caption
    
    comments_info = AnnotationCommentInfo.query.filter(AnnotationCommentInfo.file_id.in_(df.index.values))
    for ci in comments_info:
        df.loc[ci.file_id, ['Comments by ' + annotators_map[ci.user_id]]] = ci.comment
    
    filePath = make_file(df, experimentId, format1)
    return send_file(filePath, as_attachment=True)

@blueprint.route('/_exportResultsWide/<int:experimentId>/<string:format>', methods=['POST','GET'])
def _exportResultsWide(experimentId, format):
    """
    Exporting results at wide format, wide format is as the following:
    Col for each (label,annotator) pairs:
    Level[Label] Annotator Username: Cell = 1 in case of this label is selected by the annotator
    Args:
        experimentId: The experiment id
        format: needed format of exported results file (csv or xlsx)
    Returns:
        filePath: File in csv/xlsx format (based on sent argument)
    """
    experiment = Experiment.query.filter_by(id=experimentId).first()
    levels_map = {}
    labels_map = {}
    annotators_map = {}

    annotators_assoc = experiment.annotators
    for assoc in annotators_assoc:
        annotators_map[assoc.annotator.id] = assoc.annotator.username
    
    levels = experiment.annotation_levels
    for level in levels:
        levels_map[level.id] = level.name
        for label in level.labels:
            labels_map[label.id] = label.name
    
    files = File.query.filter_by(experiment_id = experimentId)\
            .join(FileCaption, FileCaption.file_id == File.id)\
            .with_entities(File.id, File.name, File.content,\
            File.edge_link, File.concordance_lineNumber, File.display_order,\
            FileCaption.caption, FileCaption.target_caption,)\
            .all()
    ZERO_COL = len(files) * [0]
    EMPTY_COL = len(files) * [""]
    df = pd.DataFrame(files)
    df.columns = ["id", "name", "content","edge_link", "concordance_lineNumber", "display_order", "caption","target_caption"]
    df.set_index('id',inplace=True)
    for annotator in annotators_map.values():
        for level in levels:
            for label in level.labels:
                df[f'{level.name}({label.name}) {annotator}'] = ZERO_COL
                df[f'coordinates of {level.name}({label.name}) {annotator}'] = EMPTY_COL
    
    annotations_info = AnnotationInfo.query.filter(AnnotationInfo.annotationLevel_id.in_(levels_map.keys()))
    for a in annotations_info:
        labelcol = f'{levels_map[a.annotationLevel_id]}({labels_map[a.label_id]}) {annotators_map[a.user_id]}'
        coordcol = f'coordinates of {levels_map[a.annotationLevel_id]}({labels_map[a.label_id]}) {annotators_map[a.user_id]}'
        df.loc[a.file_id,[labelcol]] = 1
        if a.coordinates:
            df.loc[a.file_id,[coordcol]] = json.dumps(a.coordinates)
    df.rename(columns={'name': 'file_name'}, inplace=True)
    
    if experiment.uploadType == 'fromConcordance':
        experimentDIR = os.path.join(app.config['UPLOAD_FOLDER'], str(experiment.id))
        inputConcordance = os.path.join(experimentDIR, 'concordance.csv')
        data = pandas.read_csv(inputConcordance)
        if "Structure ``text_file''" not in data.columns:
            df.drop(columns=['edge_link'], inplace=True)
        data.index += 1
        df = pd.merge(df, data, how='inner', left_on=['concordance_lineNumber'], right_index=True)
    else:
        # It is included only in case of corcodance uploaded new scape style
        df.drop(columns=['edge_link'], inplace=True)
    df.drop('concordance_lineNumber', inplace=True, axis=1)
    filePath = make_file(df, experimentId, format)
    return send_file(filePath, as_attachment=True)

@blueprint.route('/_exportResultsLong/<int:experimentId>/<string:format>', methods=['POST','GET'])
def _exportResultsLong(experimentId, format):
    """
    Exporting results at long format, long format is as the following:
    For each annotation info entry there exist a corresponding record
    Columns are as the following
        level_name, label_name, label_other, annotator_username, file_name
    Args:
        experimentId: The experiment id
        format: needed format of exported results file (csv or xlsx)
    Returns:
        filePath: File in csv/xlsx format (based on sent argument)
    """
    annotation_levels = AnnotationLevel.query.filter_by(experiment_id=experimentId).with_entities(AnnotationLevel.id).all()
    result = []
    for annotation_level in annotation_levels:
        annotation_level = annotation_level[0]
        result.extend(AnnotationInfo.query.filter_by(annotationLevel_id=annotation_level)
        .join(AnnotationLevel, AnnotationInfo.annotationLevel_id == AnnotationLevel.id)
        .join(Label, Label.id == AnnotationInfo.label_id)
        .join(File, File.id == AnnotationInfo.file_id)
        .join(User, User.id == AnnotationInfo.user_id)
        .with_entities()
        .add_columns(AnnotationLevel.name, Label.name, AnnotationInfo.label_other, AnnotationInfo.coordinates, User.username, File.name)
        .order_by(AnnotationInfo.id)
        .all()
        )
    df = pd.DataFrame(result)
    df.columns = ["level_name", "label_name", "label_other", "coordinates" ,"annotator_username", "file_name"]
    # Annotation selection order column
    annotation_order = [1] * len(df)
    for i in range(1, len(df)):
        if  df.iloc[i]['level_name'] == df.iloc[i-1]['level_name'] and \
            df.iloc[i]['file_name'] == df.iloc[i-1]['file_name'] and \
            df.iloc[i]['annotator_username'] == df.iloc[i-1]['annotator_username'] :
        # if prev. annotation is same as current annotation 
        # in(level_name, file_name and annotator_username)
        # it means it is multi-choice level selected so we need to increment.
            annotation_order[i] =  annotation_order[i-1] + 1
    df['annotation_order'] = annotation_order
    filePath = make_file(df, experimentId, format)
    return send_file(filePath, as_attachment=True)

def make_file(df, experimentId, format):
    """ Utility function for making results file functions
    Args:
        df: DataFrame sheet of the results to be exported
        experimentId: experiment id, exported file name
        format: csv or xlsx sheets file format
    Returns:
        filePath: path of the exported file to be sent.
    """
    if format == '.xlsx':
        filename = str(experimentId) + '.xlsx'
        filePath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        with pandas.ExcelWriter(filePath, date_format='YYYY-MM-DD', datetime_format='YYYY-MM-DD HH:MM:SS') as writer:
            df.to_excel(writer, sheet_name='Sheet1', index=False)
    else:
        filename = str(experimentId) + '.csv'
        filePath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        df.to_csv(filePath, index=False)
    return filePath
