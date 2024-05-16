from flask import render_template, flash, redirect, url_for, request, jsonify, \
    current_app, abort, send_from_directory
from flask_babelex import lazy_gettext as _
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.sql.expression import label

from rapidannotator import db
from rapidannotator import bcrypt
from rapidannotator.models import User, Experiment, AnnotatorAssociation, File, \
    AnnotationInfo, AnnotationLevel, Label, FileCaption, AnnotationCaptionInfo, Clustering, AnnotationCommentInfo
from rapidannotator.modules.annotate_experiment import blueprint
from .api import isAnnotator

from sqlalchemy import and_
import json, csv, os, random

@blueprint.before_request
def before_request():
    if current_app.login_manager._login_disabled:
        pass
    elif not current_user.is_authenticated:
        return "Please login to access this page."

@blueprint.route('/a/<int:experimentId>')
@isAnnotator
def index(experimentId):

    experiment = Experiment.query.filter_by(id=experimentId).first()
    if experiment.category == 'elan':
        return redirect(url_for('elan.index', experimentId = experimentId))
    annotatorInfo = AnnotatorAssociation.query.filter_by(user_id=current_user.id).\
                    filter_by(experiment_id=experimentId).first()
    keyBindingDict, skipLevelDict = makeKeyBindingDict(experimentId)
    currentFileIndex = annotatorInfo.current
    firstFile = annotatorInfo.start
    lastFile = annotatorInfo.end

    if lastFile == -1:
        lastFile = experiment.files.count()

    ''' It is to make compatible with 0-based indexing '''
    lastFile = lastFile - firstFile - 1

    if currentFileIndex <= lastFile:
        currentFile = _getFile(experimentId, currentFileIndex, firstFile)
    else:
        currentFile = []

    if (annotatorInfo.end - annotatorInfo.start == annotatorInfo.current):
        is_done = int(1)
    else:
        is_done = int(0)
    ''' TODO! move current back to original value if any file was deleted '''

    ''' For displaying a warning if the labels got changed at any time'''
        
    labelCount = 0
    labelWarning = 0

    annotationLevels = AnnotationLevel.query.filter_by(experiment_id=experimentId).all()
    for level in annotationLevels:
        labels = Label.query.filter_by(annotation_id=level.id)
        labelCount += labels.count()
 
    if experiment.countLabel != labelCount and experiment.countLabel != -1 and labelCount != 0:
        if currentFileIndex > 0:
            labelWarning = 1
    
    if labelCount == 0:
        experiment.countLabel = -1
        db.session.commit()
    else:
        experiment.countLabel = labelCount
        db.session.commit()

    if lastFile == -1:
        progress_width = 0
    else:
        progress_width = round((currentFileIndex/ (lastFile  + 1))*100, 2)

    isExpowner =  int((current_user in  experiment.owners))

    return render_template('annotate_experiment/main.html',
        experiment = experiment,
        currentFile = currentFile,
        currentFileIndex = currentFileIndex,
        lastFile = lastFile,
        firstFile = firstFile,
        keyBindingDict = keyBindingDict,
        is_done = is_done,
        labelCount = labelCount,
        labelWarning = labelWarning,
        progress_width = progress_width,
        skipLevelDict = skipLevelDict,
        isExpowner = isExpowner,
        userId = current_user.id,
    ) 

def makeKeyBindingDict(experimentId):
    levels = AnnotationLevel.query.filter_by(experiment_id=\
                experimentId).order_by(AnnotationLevel.id)
    index, keyBindingDict, skipLevelDict = 1, {}, {}

    for level in levels:
        labels = Label.query.filter_by(annotation_id=level.id).all()
        keySet, labelDict, skipDict = [], {}, {}

        for label in labels:
            if label.key_binding:
                keySet.append(label.key_binding)

        for label in labels:
            if not label.key_binding:
                defaultKey = getDefaultKey(keySet)
                keySet.append(defaultKey)

            key = label.key_binding if label.key_binding else defaultKey
            labelDict[label.id] = key
            skipDict[label.id] = int(label.skip)

        keyBindingDict[index] = labelDict
        skipLevelDict[level.id] = skipDict
        index += 1

    return keyBindingDict, skipLevelDict

def getDefaultKey(keySet):
    for i in range(26):
        k = chr(i + 97)
        if k not in keySet:
            return k
    return ''

def getContextBTAT(string):
    txt = string.split("/")
    context = txt[0].split('_')[0]
    unaligned = False
    if txt[2] == 'NA':
        before_time = float(txt[1])
        unaligned = True
    else:
        try:
            before_time = float(txt[1]) + (float(txt[2])/100.0)
        except:
            before_time = float(txt[2])
            unaligned = True
    if txt[4] == 'NA':
        after_time = float(txt[3])
        unaligned = True
    else:
        try:
            after_time = float(txt[3]) + (float(txt[4])/100.0)
        except:
            after_time = float(txt[4])
            unaligned = True
    return context, before_time, after_time, unaligned


def getRequiredCaption(time_limit, context, operator):
    req_caption = ''
    for cpt in context:
        caption, bt, at, _ = getContextBTAT(cpt)
        if operator(bt, time_limit):
            break
        req_caption = req_caption + caption + ' '
    return req_caption

def getRequiredCaptionUnaligned(time_limit, context):
    req_caption = ''
    limit = int(time_limit*float(3))
    for cpt in context:
        caption, bt, at, _ = getContextBTAT(cpt)
        if limit <= 0:
            break
        req_caption = req_caption + caption + ' '
        limit = limit - 1
    return req_caption


def get_tagged_context(inputPath, concordance_line_number, before_time, after_time):
    with open(inputPath, encoding='utf-8') as tsvfile:
            in_txt = csv.reader(tsvfile, delimiter = ',')
            text = list(in_txt)[concordance_line_number]
            query_final_text = text[3]
            tagged_context_before, tagged_query_item, tagged_context_after = text[5], text[6], text[7]
            tagged_context_before = tagged_context_before.split(' ')
            tagged_context_before.reverse()
            tagged_context_after = tagged_context_after.split(' ')
    tsvfile.close()
    tagged_query_item = tagged_query_item.split(' ')
    query_context1, query_bt1, query_at1, unaligned1 = getContextBTAT(tagged_query_item[0])
    query_context2, query_bt2, query_at2, unaligned2 = getContextBTAT(tagged_query_item[-1])
    import operator as op
    if not unaligned1:
        left_caption = getRequiredCaption(query_bt1 - float(before_time) - 0.8, tagged_context_before, op.lt)
        right_caption = getRequiredCaption(query_bt2 + float(after_time) + 0.8, tagged_context_after, op.gt)
    else:
        left_caption = getRequiredCaptionUnaligned(float(before_time), tagged_context_before)
        right_caption = getRequiredCaptionUnaligned(float(after_time), tagged_context_after)
    left_caption = left_caption.split(' ')
    left_caption.reverse()
    req_tagged_caption = ' '.join(left_caption) + ' ' + query_final_text + ' ' + right_caption
    return req_tagged_caption


'''
    .. params:
        experimentId: id of the experiment
        fileIndex: index of the file to fetch
'''
def _getFile(experimentId, fileIndex, start):
    
    experiment = Experiment.query.filter_by(id=experimentId).first()
    if experiment.displayType == 'fcfs':
        clustering = Clustering.query.filter_by(experiment_id = experimentId).first()
        if clustering is not None:
            if (int(clustering.status) == 2) and clustering.display:
                from rapidannotator import app
                experimentDIR = os.path.join(app.config['UPLOAD_FOLDER'], str(experimentId))
                outJson = os.path.join(experimentDIR, 'output.json')
                with open(outJson, 'r') as json_file:
                    json_dict = json.load(json_file)
                sort_order = json_dict['sortOrder']
                file_ids = json_dict['file_ids']
                curr_id = file_ids[sort_order[fileIndex + start]]
                currentFile = File.query.filter_by(id = int(curr_id)).first()
        else:
            currentFile = experiment.files.order_by(File.id)[fileIndex + start]
    else:
        currentFile = experiment.files.order_by(File.display_order)[fileIndex + start]
    
    cp = FileCaption.query.filter_by(file_id = currentFile.id).first()
    caption_info = AnnotationCaptionInfo.query.filter_by(user_id=current_user.id, file_id=currentFile.id).first()
    if caption_info == None:
        target_caption = cp.target_caption
    else:
        target_caption = caption_info.target_caption

    comment_info = AnnotationCommentInfo.query.filter_by(user_id=current_user.id, file_id=currentFile.id).first()
    if comment_info == None:
        comment = ""
    else:
        comment = comment_info.comment
    
    if (experiment.uploadType == 'fromConcordance') and (experiment.category == "video" or experiment.category == "audio"):
        from rapidannotator import app
        experimentDIR = os.path.join(app.config['UPLOAD_FOLDER'], str(experimentId))
        inputConcordance = os.path.join(experimentDIR, 'concordance.csv')
        tagged_caption = get_tagged_context(inputConcordance, currentFile.concordance_lineNumber, \
            experiment.display_time.before_time, experiment.display_time.after_time)
    else:
        tagged_caption = cp.caption

    currentFile = {
        'id' : currentFile.id,
        'name' : currentFile.name,
        'content' : currentFile.content,
        'caption' : tagged_caption,
        'target_caption': target_caption,
        'edge_link': currentFile.edge_link,
        'comment': comment,
    }
    return currentFile

'''
    .. updates the value of current to the value given in params
'''
def _updateCurrentFileIndex(experimentId, currentFileIndex):
    annotatorInfo = AnnotatorAssociation.query.filter(and_\
                        (AnnotatorAssociation.user_id==current_user.id, \
                        AnnotatorAssociation.experiment_id==experimentId)).first()

    annotatorInfo.current = currentFileIndex
    db.session.commit()
    response = {}
    response['success'] = True

    return jsonify(response)

'''
    wrapper over _updateCurrentFileIndex that will be called by client
'''
@blueprint.route('/updateCurrentFileIndex', methods=['POST','GET','PUT'])
def updateCurrentFileIndex():

    ''' in PUT request data is received in request.form '''
    experimentId = request.form.get('experimentId', None)
    currentFileIndex = request.form.get('currentFileIndex', None)

    _updateCurrentFileIndex(experimentId, int(currentFileIndex))
    response = {}
    response['success'] = True

    return jsonify(response)


''' delete the annotation of the specified file & experiment '''
@blueprint.route('/deleteAnnotation', methods=['DELETE'])
def deleteAnnotation():

    ''' in DELETE request data is received in request.form '''
    experimentId = request.form.get('experimentId', None)
    fileId = request.form.get('fileId', None)
    lp = request.form.get('lp', None)
    if int(lp) == 0:
        fileId = int(fileId) - 1

    AnnotationInfo.query.filter(and_(AnnotationInfo.user_id==current_user.id, \
                                    AnnotationInfo.file_id==fileId)\
                                    ).delete()
    
    db.session.commit()
    response = {}
    response['success'] = True

    return jsonify(response)


'''
    wrapper over _getFile that will be called by client
'''
@blueprint.route('/_getFileDetails', methods=['POST','GET'])
def _getFileDetails():
    experimentId = request.args.get('experimentId', None)
    currentFileIndex = request.args.get('currentFileIndex', None)
    firstFile = request.args.get('firstFile', None)
    currentFile = _getFile(experimentId, int(currentFileIndex), int(firstFile))
    return jsonify(currentFile)

''' (TODO) correct names '''
@blueprint.route('/uploads/<path:filename>')
def download_file(filename):
    from rapidannotator import app
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@blueprint.route('/_addAnnotationInfo', methods=['POST','GET'])
def _addAnnotationInfo():

    for k in request.args:
        arguments = json.loads(k)

    fileId = arguments.get('fileId', None)
    annotations = arguments.get('annotations')
    annotationsOrder = arguments.get('annotationsOrder')
    prevLabelCount = int(arguments.get('labelCount', None))
    userId = int(arguments.get('userId', None))
    hasToIncreaseCurrent = int(arguments.get('hasToIncreaseCurrent', None))
    coordinates = arguments.get('coordinates')
    comment = arguments.get('comment')
    
    if coordinates == None:
        coordinates = {}

    if comment is not None:
        commentInfo = AnnotationCommentInfo.query.filter_by(user_id=current_user.id, file_id=fileId).first()
        if commentInfo is not None:
            AnnotationCommentInfo.query.filter(and_(AnnotationCommentInfo.user_id==userId, AnnotationCommentInfo.file_id==fileId)).delete()
            db.session.commit()
        commentInfo = AnnotationCommentInfo(
            file_id = fileId,
            user_id = userId,
            comment = comment
        )
        db.session.add(commentInfo)

    # targetCaptionData = arguments.get('targetCaptionData', None)

    ''' For displaying a warning if the labels got changed at any time'''
    labelCount = 0

    fileItem = File.query.filter_by(id=fileId).first()
    # fileItem.target_caption = targetCaptionData

    experimentId = fileItem.experiment_id
    
    annotationLevels = AnnotationLevel.query.filter_by(experiment_id=experimentId).all()
    for level in annotationLevels:
        labels = Label.query.filter_by(annotation_id=level.id)
        labelCount += labels.count()

    if labelCount != prevLabelCount:
        response = {}
        response['success'] = False
        return jsonify(response)

    annotationInfo = AnnotationInfo.query.filter_by(user_id=current_user.id, file_id=fileId).all()
    if annotationInfo is not None:
        AnnotationInfo.query.filter(and_(AnnotationInfo.user_id==userId, AnnotationInfo.file_id==fileId)).delete()
        db.session.commit()
    for levelId, labels in annotations.items():
        for labelId in annotationsOrder[levelId]:
            annotationInfo = AnnotationInfo(
                file_id = fileId,
                annotationLevel_id = levelId,
                label_id = labelId,
                user_id = userId,
                label_other = labels[str(labelId)],
                coordinates = coordinates.get(str(levelId))
            )
            db.session.add(annotationInfo)

    if hasToIncreaseCurrent == 1:
        annotatorInfo = AnnotatorAssociation.query.filter_by(user_id=current_user.id).\
            filter_by(experiment_id=experimentId).first()
        annotatorInfo.current = annotatorInfo.current + 1

    db.session.commit()

    response = {}
    response['success'] = True

    return jsonify(response)


@blueprint.route('/_toggleLooping', methods=['POST','GET'])
def _toggleLooping():

    current_user.looping = not current_user.looping
    db.session.commit()

    response = {}
    response['success'] = True

    return jsonify(response)

''' Change the status of the experiment dynamically'''
@blueprint.route('/checkStatus', methods=['POST'])
def checkStatus():
    experimentId = request.form.get('experimentId', None)
    associations = AnnotatorAssociation.query.filter_by(experiment_id=experimentId).all()
    associationsCount = AnnotatorAssociation.query.filter_by(experiment_id=experimentId).count()
    experiment = Experiment.query.filter_by(id=experimentId).first()
    fileCount = experiment.files.count()
    req = 0
    for association in associations:
        if association.end == -1:
            req += fileCount
        else:
            req += association.end - association.start
    tot = 0
    for association in associations:
        tot += association.current

    if req == tot:
        experiment.status = 'Completed'
        experiment.is_done = 1
        db.session.commit()
    response = {}
    response['success'] = True
    return jsonify(response)


@blueprint.route('/saveTargetCaption', methods=["POST"])
def saveTargetCaption():
    fileId = request.form.get('fileId', None)
    targetCaption = request.form.get('targetCaption', None)
    annotationCaptionInfo = AnnotationCaptionInfo.query.filter_by(file_id=fileId, user_id=current_user.id).first()
    if annotationCaptionInfo == None:
        annotationCaptionInfo = AnnotationCaptionInfo(file_id=fileId, user_id=current_user.id, target_caption=targetCaption)
        db.session.add(annotationCaptionInfo)
        db.session.commit()
    else:
        annotationCaptionInfo.target_caption = targetCaption
        db.session.commit()

    response = {}
    response['success'] = True
    return jsonify(response)

@blueprint.route('/saveFileComment', methods=["POST"])
def saveFileComment():
    fileId = request.form.get('fileId', None)
    comment = request.form.get('comment', None)
    annotationCommentInfo = AnnotationCommentInfo.query.filter_by(file_id=fileId, user_id=current_user.id).first()
    if annotationCommentInfo == None:
        annotationCommentInfo = AnnotationCommentInfo(file_id=fileId, user_id=current_user.id, comment=comment)
        db.session.add(annotationCommentInfo)
        db.session.commit()
    else:
        annotationCommentInfo.comment = comment
        db.session.commit()

    response = {}
    response['success'] = True
    return jsonify(response)


@blueprint.route('/_getSpecificFileDetails', methods=['POST','GET'])
def _getSpecificFileDetails():
    experimentId = request.args.get('experimentId', None)
    fileId = request.args.get('fileId', None)
    currentFile = _getSpecificFile(experimentId, int(fileId))
    return jsonify(currentFile)


def _getSpecificFile(experimentId, fileId):
    
    experiment = Experiment.query.filter_by(id=experimentId).first()
    currentFile = File.query.filter_by(id=fileId).first()
    
    cp = FileCaption.query.filter_by(file_id = fileId).first()
    caption_info = AnnotationCaptionInfo.query.filter_by(user_id=current_user.id, file_id=currentFile.id).first()
    if caption_info == None:
        target_caption = cp.target_caption
    else:
        target_caption = caption_info.target_caption
    
    if (experiment.uploadType == 'fromConcordance') and (experiment.category == "video" or experiment.category == "audio"):
        from rapidannotator import app
        experimentDIR = os.path.join(app.config['UPLOAD_FOLDER'], str(experimentId))
        inputConcordance = os.path.join(experimentDIR, 'concordance.csv')
        tagged_caption = get_tagged_context(inputConcordance, currentFile.concordance_lineNumber, \
            experiment.display_time.before_time, experiment.display_time.after_time)
    else:
        tagged_caption = cp.caption

    currentFile = {
        'id' : currentFile.id,
        'name' : currentFile.name,
        'content' : currentFile.content,
        'caption' : tagged_caption,
        'target_caption': target_caption,
        'edge_link': currentFile.edge_link,
    }
    return currentFile

@blueprint.route('/specificAnnotation/<int:userId>/<int:experimentId>/<int:fileId>', methods=['GET', 'POST'])
def specificAnnotation(userId, experimentId, fileId):
    experiment = Experiment.query.filter_by(id=experimentId).first()
    annotatorInfo = AnnotatorAssociation.query.filter_by(user_id=current_user.id).\
                    filter_by(experiment_id=experimentId).first()
    keyBindingDict, skipLevelDict = makeKeyBindingDict(experimentId)

    currentFile = _getSpecificFile(experimentId, fileId)
        
    labelCount = 0

    annotationLevels = AnnotationLevel.query.filter_by(experiment_id=experimentId).all()
    for level in annotationLevels:
        labels = Label.query.filter_by(annotation_id=level.id)
        labelCount += labels.count()
    
    if labelCount == 0:
        experiment.countLabel = -1
        db.session.commit()
    else:
        experiment.countLabel = labelCount
        db.session.commit()

    annotationAlreadyDone = {}
    anno_info = AnnotationInfo.query.filter_by(user_id= userId, file_id= fileId).all()
    if len(anno_info) == 0:
        displayAlreadyAnnotated = 0
    else:
        displayAlreadyAnnotated = 1
    for info in anno_info:
        label = Label.query.filter_by(id=info.label_id).first()
        if not annotationAlreadyDone.get(info.annotationLevel_id, None):
            annotationAlreadyDone[info.annotationLevel_id] = []
        annotationAlreadyDone[info.annotationLevel_id].append([info.label_id, label.name, info.label_other, info.coordinates or []])

    isExpowner =  int((current_user in  experiment.owners))
    commentInfo = AnnotationCommentInfo.query.filter_by(user_id=current_user.id, file_id=fileId).first()
    comment = ""
    if commentInfo is not None:
        comment = commentInfo.comment
    return render_template('annotate_experiment/specific.html',
        experiment = experiment,
        currentFile = currentFile,
        keyBindingDict = keyBindingDict,
        labelCount = labelCount,
        skipLevelDict = skipLevelDict,
        isExpowner = isExpowner,
        fileId = fileId,
        userId = userId,
        annotationAlreadyDone = annotationAlreadyDone,
        displayAlreadyAnnotated = displayAlreadyAnnotated,
        comment=comment,
    )

