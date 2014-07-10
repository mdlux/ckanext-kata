# coding=utf8
"""
Controllers for Kata.
"""

import json
import logging
import string
import mimetypes
import functionally as fn
from rdflib.namespace import XSD
from rdflib.term import Identifier, URIRef, Literal, BNode
import urllib2

from paste.deploy.converters import asbool
from pylons import response, config, request, session, g
from pylons.decorators.cache import beaker_cache
from pylons.i18n import _

from ckan.controllers.api import ApiController
from ckan.controllers.package import PackageController
from ckan.controllers.user import UserController
import ckan.lib.i18n
from ckan.lib.base import BaseController, c, h, redirect, render
from ckan.lib.email_notifications import send_notification
from ckan.logic import get_action
import ckan.model as model
from ckan.model import Package, User, Related, meta, license
from ckan.model.authz import add_user_to_role
from ckanext.kata.model import KataAccessRequest
from ckanext.kata.urnhelper import URNHelper
from ckanext.kata.vocab import DC, FOAF, RDF, RDFS, Graph
import ckan.lib.captcha as captcha

_get_or_bust = ckan.logic.get_or_bust

log = logging.getLogger('ckanext.kata.controller')

# BUCKET = config.get('ckan.storage.bucket', 'default')


def get_package_owner(package):
    """Returns the user id of the package admin for the specified package.
       If multiple user accounts are associated with the package as admins,
       an arbitrary one is returned.
    """
    userid = None
    for role in package.roles:
        if role.role == "admin":
            userid = role.user_id
            break
    return userid


class MetadataController(BaseController):
    '''
    URN export
    '''

    @beaker_cache(type="dbm", expire=86400)
    def urnexport(self):
        response.headers['Content-type'] = 'text/xml'
        return URNHelper.list_packages()


class KATAApiController(ApiController):
    '''
    Functions for autocomplete fields in add dataset form
    '''

    def media_type_autocomplete(self):
        query = request.params.get('incomplete', '')
        known_types = set(mimetypes.types_map.values())
        matches = [ type_label for type_label in known_types if string.find(type_label, query) != -1 ]
        result_set = {
            'ResultSet': {
                'Result': [{'Name': label} for label in matches]
            }
        }
        return self._finish_ok(result_set)

    def tag_autocomplete(self):
        query = request.params.get('incomplete', '')
        return self._onki_autocomplete(query, "koko")

    def discipline_autocomplete(self):
        query = request.params.get('incomplete', '')
        return self._onki_autocomplete(query, "okm-tieteenala")

    def location_autocomplete(self):
        query = request.params.get('incomplete', '')
        return self._onki_autocomplete(query, "paikat")

    def _onki_autocomplete(self, query, vocab):
        url_template = "http://dev.finto.fi/rest/v1/search?query={q}*&vocab={v}"

        labels = []
        if query:
            url = url_template.format(q=query, v=vocab)
            data = urllib2.urlopen(url).read()
            jsondata = json.loads(data)
            if u'results' in jsondata:
                results = jsondata['results']
                labels = [concept['prefLabel'].encode('utf-8') for concept in results]

        result_set = {
            'ResultSet': {
                'Result': [{'Name': label} for label in labels]
            }
        }
        return self._finish_ok(result_set)


class AccessRequestController(BaseController):
    '''
    AccessRequestController class provides a feature to ask
    for editor rights to a dataset. On the other hand,
    it also provides the process to grant them upon request.
    '''

    def _have_pending_requests(self, pkg_id, user_id):
        """
        Returns whether there are already pending requests
        from the given user regarding the given package.
        """

        pending_requests = model.Session.query(KataAccessRequest).filter(
            KataAccessRequest.pkg_id == pkg_id, KataAccessRequest.user_id == user_id)
        return pending_requests.count() > 0

    def create_request(self, pkg_id):
        """
        Creates a new editor access request in the database.
        """

        url = h.url_for(controller='package', action='read', id=pkg_id)

        pkg = Package.get(pkg_id)
        pkg_title = pkg.title if pkg.title else pkg.name

        user = c.userobj if c.userobj else None
        if user:
            if not self._have_pending_requests(pkg_id, user.id):
                req = KataAccessRequest(user.id, pkg.id)
                req.save()
                h.flash_success(_("A request for editing privileges will be sent to the administrator of package %s") % pkg_title)
                redirect(url)
            else:
                h.flash_error(_("A request is already pending"))
                redirect(url)
        else:
            h.flash_error(_("You must be logged in to request edit access"))
            redirect(url)

    def unlock_access(self, id):
        q = model.Session.query(KataAccessRequest)
        q = q.filter_by(id=id)
        req = q.first()
        if req:
            user = User.get(req.user_id)
            pkg = Package.get(req.pkg_id)
            pkg_title = pkg.title if pkg.title else pkg.name
            add_user_to_role(user, 'editor', pkg)
            url = h.url_for(controller='package', action='read', id=req.pkg_id)
            h.flash_success(_("%s now has editor rights to package %s" % (user.name, pkg_title)))
            req.delete()
            meta.Session.commit()
            redirect(url)
        else:
            h.flash_error(_("No such request found!"))
            redirect('/')

    def render_edit_request(self, pkg_id):
        """
        Renders a page for requesting editor access to the dataset.
        """

        url = h.url_for(controller='package', action='read', id=pkg_id)

        c.package = Package.get(pkg_id)
        c.package_owner = get_package_owner(c.package)
        user = c.userobj if c.userobj else None

        if user:
            if not self._have_pending_requests(pkg_id, user.id):
                return render('contact/edit_request_form.html')
            else:
                h.flash_error(_("A request is already pending"))
                redirect(url)
        else:
            h.flash_error(_("You must be logged in to request edit access"))
            redirect(url)


'''DataMiningController is here for reference, some stuff like the file parsing might be useful'''
# class DataMiningController(BaseController):
#     '''
#     Controller for scraping metadata content from files.
#     '''
#
#     def read_data(self, id, resource_id):
#         """
#         Scrape all words from a file and save it to extras.
#         """
#         res = Resource.get(resource_id)
#         pkg = Package.get(id)
#         c.pkg_dict = pkg.as_dict()
#         c.package = pkg
#         c.resource = get_action('resource_show')({'model': model},
#                                                      {'id': resource_id})
#         label = res.url.split(config.get('ckan.site_url') + '/storage/f/')[-1]
#         label = urllib2.unquote(label)
#         ofs = get_ofs()
#
#         try:
#             # Get file location
#             furl = ofs.get_url(BUCKET, label).split('file://')[-1]
#         except FileNotFoundException:
#             h.flash_error(_('Cannot do data mining on remote resource!'))
#             url = h.url_for(controller='package', action='resource_read',
#                             id=id, resource_id=resource_id)
#             return redirect(url)
#
#         wordstats = {}
#         ret = {}
#
#         if res.format in ('TXT', 'txt'):
#             wdsf, wdspath = tempfile.mkstemp()
#             os.write(wdsf, "%s\nmetadata description title information" % furl)
#
#             with os.fdopen(wdsf, 'r') as wordfile:
#                 preproc = orngText.Preprocess()
#                 table = orngText.loadFromListWithCategories(wdspath)
#                 data = orngText.bagOfWords(table, preprocessor=preproc)
#                 words = orngText.extractWordNGram(data, threshold=10.0, measure='MI')
#
#             for i in range(len(words)):
#                 d = words[i]
#                 wordstats = d.get_metas(str)
#
#             for k, v in wordstats.items():
#                 if v.value > 10.0:
#                     ret[unicode(k, 'utf8')] = v.value
#
#             c.data_tags = sorted(ret.iteritems(), key=itemgetter(1), reverse=True)[:30]
#             os.remove(wdspath)
#
#             for i in range(len(data)):
#                     d = words[i]
#                     wordstats = d.get_metas(str)
#
#             words = []
#             for k, v in wordstats.items():
#                 words.append(k)
#
#             # Save scraped words to extras.
#
#             model.repo.new_revision()
#             if not 'autoextracted_description' in pkg.extras:
#                 pkg.extras['autoextracted_description'] = ' '.join(words)
#
#             pkg.save()
#
#             return render('datamining/read.html')
#
#         elif res.format in ('odt', 'doc', 'xls', 'ods', 'odp', 'ppt', 'doc', 'html'):
#
#             textfd, textpath = convert_to_text(res, furl)
#
#             if not textpath:
#                 h.flash_error(_('This file could not be mined for any data!'))
#                 os.close(textfd)
#                 return render('datamining/read.html')
#             else:
#                 wdsf, wdspath = tempfile.mkstemp()
#                 os.write(wdsf, "%s\nmetadata description title information" % textpath)
#                 preproc = orngText.Preprocess()
#                 table = orngText.loadFromListWithCategories(wdspath)
#                 data = orngText.bagOfWords(table, preprocessor=preproc)
#                 words = orngText.extractWordNGram(data, threshold=10.0, measure='MI')
#
#                 for i in range(len(words)):
#                     d = words[i]
#                     wordstats = d.get_metas(str)
#
#                 for k, v in wordstats.items():
#                     if v.value > 10.0:
#                         ret[unicode(k, 'utf8')] = v.value
#
#                 c.data_tags = sorted(ret.iteritems(), key=itemgetter(1), reverse=True)[:30]
#                 os.close(textfd)
#                 os.close(wdsf)
#                 os.remove(wdspath)
#                 os.remove(textpath)
#
#                 for i in range(len(data)):
#                     d = words[i]
#                     wordstats = d.get_metas(str)
#
#                 words = []
#
#                 for k, v in wordstats.items():
#                     log.debug(k)
#                     words.append(substitute_ascii_equivalents(k))
#
#                 model.repo.new_revision()
#
#                 if not 'autoextracted_description' in pkg.extras:
#                     pkg.extras['autoextracted_description'] = ' '.join(words)
#
#                 pkg.save()
#
#                 return render('datamining/read.html')
#         else:
#             h.flash_error(_('This metadata document is not in proper format for data mining!'))
#             url = h.url_for(controller='package', action='resource_read',
#                             id=id, resource_id=resource_id)
#             return redirect(url)
#
#     def save(self):
#         if not c.user:
#             return
#
#         model.repo.new_revision()
#         data = clean_dict(unflatten(tuplize_dict(parse_params(request.params))))
#         package = Package.get(data['pkgid'])
#         keywords = []
#         context = {'model': model, 'session': model.Session,
#                    'user': c.user}
#
#         if check_access('package_update', context, data_dict={"id": data['pkgid']}):
#
#             for k, v in data.items():
#                 if k.startswith('kw'):
#                     keywords.append(v)
#
#             tags = package.get_tags()
#
#             for kw in keywords:
#                 if not kw in tags:
#                     package.add_tag_by_name(kw)
#
#             package.save()
#             url = h.url_for(controller='package', action='read', id=data['pkgid'])
#             redirect(url)
#         else:
#             redirect('/')


class ContactController(BaseController):
    """
    Add features to contact the dataset's owner. 
    
    From the web page, this can be seen from the link telling that this dataset is accessible by contacting the author. 
    The feature provides a form for message sending, and the message is sent via e-mail. 
    """

    def _send_message(self, recipient, email, email_dict):
        ''' Send message to given email '''
        import ckan.lib.mailer

        try:
            ckan.lib.mailer.mail_recipient(recipient, email,
                                           email_dict['subject'], email_dict['body'])
        except ckan.lib.mailer.MailerException:
            raise

    def _send_if_allowed(self, pkg_id, subject, msg, prologue=None, epilogue=None, recipient=None, email=None):
        """
        Send a contact e-mail if allowed.

        All of the arguments should be unicode strings.
        """

        package = Package.get(pkg_id)

        prologue = prologue + "\n\n\n" if prologue else ""
        epilogue = "\n\n\n" + epilogue if epilogue else ""

        full_msg = u"{a}{b}{c}".format(a=prologue, b=msg, c=epilogue)
        email_dict = {"subject": subject,
                      "body": full_msg}

        if c.user:
            owner_id = get_package_owner(package)
            if owner_id:
                owner = User.get(owner_id)
                owner_dict = owner.as_dict()
                owner_dict['name'] = owner.fullname if owner.fullname else owner.name
                if msg:
                    model.repo.new_revision()
                    if recipient == None:
                        send_notification(owner_dict, email_dict)
                    else:
                        self._send_message(recipient, email, email_dict)
                    self._mark_owner_as_contacted(c.userobj, pkg_id)
                    h.flash_notice(_("Message sent"))
                else:
                    h.flash_error(_("No message"))
            else:
                h.flash_error(_("No owner found"))
        else:
            h.flash_error(_("Please login"))

    def _mark_owner_as_contacted(self, userobj, pkg_id):
        """Mark this user as having already contacted the package owner"""

        if "contacted" not in userobj.extras:
            userobj.extras['contacted'] = []

        userobj.extras['contacted'].append(pkg_id)
        userobj.save()

    def send_contact(self, pkg_id):
        '''
        Send a user message from CKAN to dataset distributor contact.
        '''
        # Todo: replan and fix when we have multiple distributor emails available
        # This only works because we have only one contact
        prologue_template = u'{a} ({b}) has sent you a message regarding the following dataset:\
\n\n{c} (Identifier: {d})\n\nThe message is below.\n\n{a} ({b}) on lähettänyt sinulle viestin koskien tietoaineistoa:\
\n\n{c} (Tunniste: {d})\n\nViesti:\n\n    ---'

        epilogue = u'    ---\
\n\nPlease do not reply directly to this e-mail.\
\nIf you need to reply to the sender, use the direct e-mail address above.\
\n\nÄlä vastaa suoraan tähän viestiin. Jos vastaat lähettäjälle, \
käytä yllä olevaa sähköpostiosoitetta.'

        package = Package.get(pkg_id)
        package_title = package.title if package.title else package.name
        if c.userobj:
            user_name = c.userobj.fullname if c.userobj.fullname else c.userobj.name
            # Todo: this should take the specific contact address, not just the first contact email
            email_tuples = filter(lambda (k, v): k.startswith('contact_') and k.endswith('_email'), package.extras.iteritems())
            
            emails = [con[1] for con in email_tuples]
            email = fn.first(emails)
            
            # consequently, this now prints "Dear email@address.com", should be contact_0_name instead
            recipient = email

            user_msg = request.params.get('msg', '')
            prologue = prologue_template.format(a=user_name, b=c.userobj.email, c=package_title, d=package.name)

            subject = "Message regarding dataset / Viesti koskien tietoaineistoa %s" % package_title
            self._send_if_allowed(pkg_id, subject, user_msg, prologue, epilogue, recipient, email)
        else:
            h.flash_error(_("Please login"))

        url = h.url_for(controller='package',
                        action="read",
                        id=package.id)

        return redirect(url)

    def send_request(self, pkg_id):
        '''
        Send a request to access data to CKAN dataset owner.
        '''

        prologue_template = u'{a} ({b}) is requesting access to data in dataset\n\n{c} (Identifier: {d})\n\n\
for which you are currently marked as distributor.\n\nThe message is below.\n\n\
{a} ({b}) pyytää dataa, joka liittyy tietoaineistoon\n\n{c} (Tunniste: {d})\n\nja johon sinut on merkitty jakelijaksi. \
Mukaan liitetty viesti on alla.\n\n    ---'

        epilogue = u'    ---\n\nPlease do not reply directly to this e-mail.\n\
If you need to reply to the sender, use the direct e-mail address above.\n\n\
Älä vastaa suoraan tähän viestiin. Jos haluat lähettää viestin \
lähettäjälle, käytä yllä olevaa sähköpostiosoitetta.'

        package = Package.get(pkg_id)
        package_title = package.title if package.title else package.name
        if c.userobj:
            user_name = c.userobj.fullname if c.userobj.fullname else c.userobj.name

            user_msg = request.params.get('msg', '')
            prologue = prologue_template.format(a=user_name, b=c.userobj.email, c=package_title, d=package.name)

            subject = _("Data access request for dataset / Datapyynto tietoaineistolle %s" % package_title)
            self._send_if_allowed(pkg_id, subject, user_msg, prologue, epilogue)
        else:
            h.flash_error(_("Please login"))

        url = h.url_for(controller='package',
                        action="read",
                        id=package.id)

        return redirect(url)


    def render_contact(self, pkg_id):
        """
        Render the contact form if allowed.
        """

        c.package = Package.get(pkg_id)
        url = h.url_for(controller='package',
                        action="read",
                        id=c.package.id)
        if c.user:
            if pkg_id not in c.userobj.extras.get('contacted', []):
                return render('contact/contact_form.html')
            else:
                h.flash_error(_("Already contacted"))
                return redirect(url)
        else:
            h.flash_error(_("Please login"))
            return redirect(url)

    def render_request(self, pkg_id):
        """
        Render the access request contact form if allowed.
        """

        c.package = Package.get(pkg_id)
        url = h.url_for(controller='package',
                        action="read",
                        id=c.package.id)
        if c.user:
            if pkg_id not in c.userobj.extras.get('contacted', []):
                return render('contact/dataset_request_form.html')
            else:
                h.flash_error(_("Already contacted"))
                return redirect(url)
        else:
            h.flash_error(_("Please login"))
            return redirect(url)


class KataUserController(UserController):
    """
    Overwrite logged_in function in the super class.
    """

    def logged_in(self):
        """
        Minor rewrite to redirect the user to the own profile page instead of
        the dashboard.
        @return - some sort of redirect object??
        """
        # we need to set the language via a redirect
        lang = session.pop('lang', None)
        session.save()
        came_from = request.params.get('came_from', '')

        # we need to set the language explicitly here or the flash
        # messages will not be translated.
        ckan.lib.i18n.set_lang(lang)
        
        if h.url_is_local(came_from):
            return h.redirect_to(str(came_from))

        if c.user:
            context = {'model': model,
                       'user': c.user}

            data_dict = {'id': c.user}

            user_dict = get_action('user_show')(context, data_dict)

            #h.flash_success(_("%s is now logged in") %
            #                user_dict['display_name'])
            return h.redirect_to(controller='user', action='read', id=c.userobj.name)
        else:
            err = _('Login failed. Bad username or password.')
            if g.openid_enabled:
                err += _(' (Or if using OpenID, it hasn\'t been associated '
                         'with a user account.)')
            if asbool(config.get('ckan.legacy_templates', 'false')):
                h.flash_error(err)
                h.redirect_to(controller='user',
                              action='login', came_from=came_from)
            else:
                return self.login(error=err)


class KataPackageController(PackageController):
    """
    Adds advanced search feature.
    """

    def advanced_search(self):
        """
        Parse query parameters from different search form inputs, modify into
        one query string 'q' in the context and call basic search() method of
        the super class.

        @return - dictionary with keys results and count
        """
        # parse author search into q
        q_author = c.q_author = request.params.get('q_author', u'')

        # unicode format (decoded from utf8)
        q_free = c.q_free = request.params.get('q_free', u'')
        q = c.q = q_free + u' AND ' + u'author:' + q_author

        log.debug('advanced_search(): request.params.items(): %r' % request.params.items())
        #log.debug('advanced_search(): q: %r' % q)
        log.debug('advanced_search(): call to search()')
        return self.search()

    def dataset_editor_manage(self, name):
        '''
        Manages (adds) editors and admins of a dataset and sends an invitation e-mail
        if wanted in case user has not yet logged in to the service.
        The invitation email feature has no automatic features bound to it, it is a
        plain email sender.

        :name: package name
        :username: if username (string) and role (string) are set, the user is added for the role
        :role: if username (string) and role (string) are set, the user is added for the role
        :email: if email address is given, an invitation email is sent

        Renders the package_administration page via _show_dataset_role_page()

        '''
        context = {'model': model, 'session': model.Session, 'user': c.user}

        data_dict = {}
        data_dict['name'] = name

        username = request.params.get('username', False)
        email = request.params.get('email', False)
        role = request.params.get('role', False)

        if username:
            data_dict['role'] = role
            data_dict['username'] = username
            ret = get_action('dataset_editor_add')(context, data_dict)
            if not ret.get('success', None):
                h.flash_error(ret.get('msg'))
            else:
                h.flash_success(ret.get('msg'))

        if email:
            try:
                captcha.check_recaptcha(request)
                try:
                    subject = u'Invitation to use Kata metadata catalogue - kutsu käyttämään Kata-metadatakatalogia'
                    body = u'\n\n%s would like to add you to editors for package %s \
in Kata service. To enable this, please log in to the service: %s.\n\n' % (c.userobj.fullname, data_dict.get('title', ''), g.site_url)
                    body += u'\n\n%s haluaisi lisätä sinut muokkaajaksi tietoaineistoon %s \
Kata-metadatakatalogipalvelussa. Mahdollistaaksesi tämän, ole hyvä ja kirjaudu palveluun osoitteessa: %s.\n\n' \
                            % (c.userobj.fullname, data_dict.get('title', ''), g.site_url)
                    body += u'\n------------\nLähettäjän viesti / Sender\'s message:\n%s\n\n' % (request.params.get('mail_message', ''))

                    ckan.lib.mailer.mail_recipient(email, email, subject, body)
                    h.flash_success(_('Message sent'))
                except ckan.lib.mailer.MailerException:
                    raise
            except captcha.CaptchaError:
                error_msg = _(u'Bad Captcha. Please try again.')
                h.flash_error(error_msg)

        pkg = model.Package.get(name)
        data_dict = get_action('package_show')(context, {'id':pkg.id})
        data_dict['domain_object'] = pkg.id
        domain_object_ref = _get_or_bust(data_dict, 'domain_object')
        domain_object = ckan.logic.action.get_domain_object(model, domain_object_ref)

        return self._show_dataset_role_page(domain_object, context, data_dict)

    def dataset_editor_delete(self, name):
        '''
        Deletes a user from a dataset role.

        :name: dataset name
        :username: user (string) and role (string) to be deleted from dataset
        :role: user (string) and role(string) to be deleted from dataset

        redirects to dataset_editor_manage
        '''
        context = {'model': model, 'session': model.Session, 'user': c.user}
        data_dict = {}
        data_dict['name'] = name
        data_dict['username'] = request.params.get('username', None)
        data_dict['role'] = request.params.get('role', None)

        ret = ckan.logic.get_action('dataset_editor_delete')(context, data_dict)

        if not ret.get('success', None):
            h.flash_error(ret.get('msg'))
        else:
            h.flash_success(ret.get('msg'))

        h.redirect_to(h.url_for(controller='ckanext.kata.controllers:KataPackageController', action='dataset_editor_manage', name=name))

    def _roles_list(self, userobj, domain_object):
        '''
        Builds the selection of roles for the role popup menu
        '''
        if ckan.model.authz.user_has_role(userobj, 'admin', domain_object) or \
                userobj.sysadmin == True:
            return [{'text': 'Admin', 'value':'admin'},
                   {'text': 'Editor', 'value':'editor'},
                   {'text': 'Reader', 'value':'reader'}]
        else:
            return [{'text': 'Editor', 'value':'editor'},
                   {'text': 'Reader', 'value':'reader'}]

    def _show_dataset_role_page(self, domain_object, context, data_dict):
        '''
        Adds data for template and renders it
        '''

        c.roles = self._roles_list(c.userobj, domain_object)
        editor_list = get_action('roles_show')(context, data_dict)
        c.members = []

        for role in editor_list.get('roles', ''):
            q = model.Session.query(model.User).\
                filter(model.User.id == role['user_id']).first()

            c.members.append({'user_id': role['user_id'], 'user': q.name, 'role': role['role']})
        c.pkg = Package.get(data_dict['id'])

        return render('package/package_rights.html')


class KataInfoController(BaseController):
    '''
    KataInfoController provides info pages, which
    are non-dynamic and visible for all
    '''

    def render_help(self):
        '''
        Provides the help page
        '''
        return render('kata/help.html')

    def render_faq(self):
        '''
        Provides the FAQ page
        '''
        return render('kata/faq.html')

