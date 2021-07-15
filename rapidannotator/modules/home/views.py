import shutil
from flask import render_template, flash, redirect, url_for, request, jsonify, \
    current_app, abort
from flask_babelex import lazy_gettext as _
from flask_login import current_user, login_required, login_user, logout_user

from rapidannotator import db
from rapidannotator import bcrypt
import flask_bcrypt, os
from rapidannotator.models import FileCaption, User, Experiment, RightsRequest, AnnotatorAssociation
from rapidannotator.modules.home import blueprint
from rapidannotator.modules.home.forms import AddExperimentForm, UpdateInfoForm
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from io import BytesIO
import base64
from rapidannotator import app
import pandas as pd
from rapidannotator.models import File
from werkzeug.utils import secure_filename



@blueprint.before_request
def before_request():
    if current_app.login_manager._login_disabled:
        pass
    elif not current_user.is_authenticated:
        return current_app.login_manager.unauthorized()



@blueprint.route('/')
def index():
    addExperimentForm = AddExperimentForm()
    myExperiments = current_user.my_experiments.all()
    annotatorAssociation = current_user.experiments_to_annotate
    experimentsToAnnotate = [association.experiment for association in annotatorAssociation]
    
    totalExperiments = Experiment.query.all()
    othersExperiments = []
    for experiment in totalExperiments:
        owners = experiment.owners
        if (current_user in owners) and (owners.count() == 1):
            continue
        othersExperiments.append(experiment)
    
    is_admin = int(current_user.is_admin())

    return render_template('home/main.html',
        addExperimentForm = addExperimentForm,
        myExperiments = myExperiments,
        experimentsToAnnotate = experimentsToAnnotate,
        othersExperiments = othersExperiments,
        is_admin = is_admin,
        )

@blueprint.route('/addExperiment', methods=['POST'])
def addExperiment():
    addExperimentForm = AddExperimentForm()

    if addExperimentForm.validate_on_submit():
        experiment = Experiment(
            name=addExperimentForm.name.data,
            description=addExperimentForm.description.data,
            category=addExperimentForm.category.data,
            uploadType=addExperimentForm.uploadType.data,
            displayType=addExperimentForm.displayType.data,
        )
        experiment.owners.append(current_user)
        db.session.add(experiment)
        db.session.commit()

        experimentId = experiment.id
        return redirect(url_for('add_experiment.index', experimentId = experimentId, page = 1))

    errors = "addExperimentErrors"
    return render_template('home/main.html',
        addExperimentForm = addExperimentForm,
        errors = errors,)


@blueprint.route('/askRights', methods=['GET', 'POST'])
def askRights():
    message = request.args.get('message', '')
    role = request.args.get('role', 'experimenter')

    rightsRequest = RightsRequest(
        user_id = current_user.id,
        username = current_user.username,
        role = role,
        message = message,
    )

    db.session.add(rightsRequest)
    db.session.commit()

    response = {}
    response['success'] = True

    return jsonify(response)

''' check for what right do the user has / requested for '''
@blueprint.route('/checkRights', methods=['GET', 'POST'])
def checkRights():

    requestsSent = RightsRequest.query.filter_by(user_id=current_user.id)
    
    if requestsSent.count() == 0:
        response = {}
        response['success'] = False
        return jsonify(response)
    
    response = {}
    response['success'] = True

    for r in requestsSent:
        if r.role == "experimenter": response['experimenterRequest'] = True
        if r.role == "admin": response['adminRequest'] = True

    return jsonify(response)

@blueprint.route('/logout', methods=['POST'])
def logout():
    logout_user()
    return redirect(url_for('frontpage.index'))

@blueprint.route('/updateInfo', methods=['GET', 'POST'])
def updateInfo():

    user = current_user
    updateForm = UpdateInfoForm(obj=user)
    updateForm.populate_obj(user)
    
    if request.method == 'GET':
        return render_template('home/settings.html', updateForm = updateForm,\
            user = current_user)
    elif request.method == 'POST':
        updateForm1 = UpdateInfoForm()
        if updateForm1.validate_on_submit():
            user = User.query.filter_by(id=current_user.id).first()    
            user.username = updateForm1.username.data
            user.fullname = updateForm1.fullname.data
            user.email = updateForm1.email.data
            if updateForm.password.data is not None:
                hashedPassword = bcrypt.generate_password_hash(\
                    updateForm1.password.data).decode('utf-8')
                user.password = hashedPassword
            db.session.commit()
            flash(_('Information updated successfully'))
            return render_template('home/settings.html', updateForm = updateForm, \
                user = current_user)    

        errors = "UpdateErrors"
        return render_template('home/settings.html',
            updateForm = updateForm1,
            user = current_user,
            errors = errors,)

@blueprint.route('/checkProgress/<int:userId>', methods=['GET'])
def checkProgress(userId):
    experiments = current_user.my_experiments.all()
    
    barWidth = 0.35
    bars = []
    xpos = []
    names, labels = [], []
    plt.clf()
    associations = AnnotatorAssociation.query.filter_by(user_id=userId).all()

    for i, association in enumerate(associations):
        experiment = Experiment.query.filter_by(id=association.experiment_id).first()
        filesLength = experiment.files.count()
        if filesLength != 0:
            bars.append(((association.current*100)/filesLength))
            xpos.append(i+1)
            names.append(experiment.name)
            labels.append(str(association.current) + "/" + str(filesLength))

    if len(bars) == 0:
        return render_template('home/progress.html', experiments = experiments, displayImg=0)

    plt.bar(xpos, bars, width=barWidth, label='Experiment Progress')
    plt.legend()

    plt.xticks(xpos, names, rotation=90)
    for i in range(len(labels)):
        plt.text(x = xpos[i] - 0.08, y = bars[i] + 0.1, s = labels[i], size = 10)
    plt.subplots_adjust(bottom= 0.4, top = 0.98)
    plt.xlabel('Experiment Name')
    plt.ylabel('Progress in Percentage')
    plt.ylim(0, 100)
    tmpfile = BytesIO()
    plt.savefig(tmpfile, format='png')
    pngImageB64String = "data:image/png;base64,"
    encoded = base64.b64encode(tmpfile.getvalue()).decode('utf-8')
    pngImageB64String = pngImageB64String + encoded
    plt.close()

    return render_template('home/progress.html', experiments = experiments, displayImg=1, html=pngImageB64String)

@blueprint.route('/getExperimentProgressData', methods=['GET', 'POST'])
def getExperimentProgressData():
    
    experimentName = request.args.get('experimentName', None)
    experiment = Experiment.query.filter_by(name=experimentName).first()
    annotators = experiment.annotators
    filesLength = experiment.files.count()
    if filesLength != 0:
        filesLength = (100/filesLength)
    
    chartInfo = []
    chartInfo.append(['Annotator Name', 'Progress'])
    for association in annotators:
        user = User.query.filter_by(id=association.user_id).first()
        chartInfo.append([user.username, (association.current*filesLength)])

    response = {}
    response['success'] = True
    response['chartInfo'] = chartInfo
    response['size'] = len(chartInfo) - 1

    return jsonify(response)

@blueprint.route('/getUserProgressData', methods=['GET', 'POST'])
def getUserProgressData():

    associations = AnnotatorAssociation.query.filter_by(user_id=current_user.id).all()
    chartInfo = []
    chartInfo.append(['Experiment Name', 'Progress'])
    for association in associations:
        experiment = Experiment.query.filter_by(id=association.experiment_id).first()
        filesLength = experiment.files.count()
        if filesLength != 0:
            filesLength = (100/filesLength)
        chartInfo.append([experiment.name, (association.current*filesLength)])
    
    response = {}
    response['success'] = True
    response['chartInfo'] = chartInfo
    response['size'] = len(chartInfo) - 1

    return jsonify(response)


@blueprint.route('/_continueExperiment', methods=['POST'])
def _continueExperiment():
    """ Continue an experiment.
    by uploading its exported results file (wide-format).
    Args:
        experiment_id: the id of the experiment to continue.
        file: a file containing the results.
    Returns:
        A JSON response containing:
            - a boolean indicating success or failure.
            - a message indicating success or failure's reason.
    """
    experimentId = request.form.get('id', None)
    description = request.form.get('description', "")
    expName = request.form.get('name', '')
    # Checks whether the experiment exists or not
    experiment = Experiment.query.filter_by(id=experimentId).first()
    if not experiment:
        return jsonify({"success": False, "message": "Invalid experiment id"})
    # Check if the experiment name is sent on the request 
    # if not assign a new name based on the continuing experiment name
    if expName == '':
        expItr = Experiment.query.order_by(Experiment.id.desc()).first().id + 1
        expName = f'Copy of {experiment.name} {expItr}'
    # if the name already exists return the following message
    elif Experiment.query.filter_by(name=expName).first():
        return jsonify({"success": False, "message": "Experiment name already exists try another name"})
    # if the name is valid then continue the experiment and check the file upload
    # if the file uploaded dosenot exist then return the following message
    resultsFile = request.files['file']
    if 'file' not in request.files or not resultsFile:
        return jsonify({"success": False, "message": "File is not provided"})
    # check the file extension
    fileName, fileExt = os.path.splitext(resultsFile.filename)
    if fileExt != '.csv' and fileExt != '.xlsx' and fileExt != '.xls':
        return jsonify({"success": False, "message": "File extension not allowed"})
    # create new experiment
    newExperiment = Experiment(name=expName, description=description, \
            category=experiment.category,uploadType=experiment.uploadType,\
                status="In Progress", is_done=False)
    newExperiment.owners.append(current_user)
    db.session.add(newExperiment)
    db.session.commit()
    # check if the directory already exists and experiment type is not text
    # then make a new directory
    newExperimentDir = os.path.join(app.config['UPLOAD_FOLDER'],str(newExperiment.id))
    if  experiment.uploadType != "viaSpreadsheet"\
        and experiment.category != "text"\
        and not os.path.exists(newExperimentDir):
        os.makedirs(newExperimentDir)
    # parse csv format
    if fileExt == '.csv':
        resultsFile = pd.read_csv(resultsFile, sep=',')
    # parse excel format
    else:
        resultsFile = pd.read_excel(resultsFile)
        pass
    # check if file_name and caption and content are in df.columns
    if  'file_name' not in resultsFile.columns or \
        'caption' not in resultsFile.columns or \
        'content' not in resultsFile.columns or \
        'edge_link' not in resultsFile.columns or \
        'display_order' not in resultsFile.columns or\
        (experiment.uploadType == "fromConcordance" and \
        'Number of hit' not in resultsFile.columns):
        # if one of the previous columns are not in the file columns
        # return the following message
        return jsonify({"success": False, "message": "File format is not correct"})
    
    # empty cells are initilized to nan, so it is needed to be converted to empty string
    resultsFile.fillna('', inplace=True)
    # iterate over the files to be copied to follow the same structure
    # filename_id(iterator)
    fileItr = File.query.order_by(File.id.desc()).first().id + 1
    for index, file in resultsFile.iterrows():
        name = file.get('file_name')
        content = file.get('content')
        caption = file.get('caption')
        targetCaption = file.get('target_caption')
        edge_link = file.get('edge_link')
        display_order = file.get('display_order')
        fileName , fileExt = os.path.splitext(name)
        # copy file from old experiment folder to new experiment folder
        if experiment.uploadType == 'manual' and experiment.category != 'text':
            # original file path
            filePath = os.path.join(app.config['UPLOAD_FOLDER'],\
                        str(experiment.id),\
                        secure_filename(content))
            # new copy file path
            content = fileName + '_' + str(fileItr) + fileExt
            copyPath =  os.path.join(newExperimentDir, content)
            # if original file exists correctly copy it to new location
            if os.path.isfile(filePath):
                shutil.copy(filePath, copyPath)

        newFile = File(name=name, content=content,\
                        display_order=display_order,\
                        concordance_lineNumber=index+1,\
                        edge_link=edge_link)
        newExperiment.files.append(newFile)
        db.session.commit()
        newFileCaption = FileCaption(caption=caption, target_caption=targetCaption, file_id = newFile.id)
        db.session.add(newFileCaption)
        fileItr += 1
    db.session.commit()
    if experiment.uploadType == 'fromConcordance':
        columns = list(resultsFile.columns)
        startConcordance = columns.index('Number of hit')
        concordance = resultsFile[columns[startConcordance:]]
        outfilePath = os.path.join(newExperimentDir, "concordance.csv")
        concordance.to_csv(outfilePath, index=False)
    return jsonify({"success": True, "message": "Experiment created successfully."})